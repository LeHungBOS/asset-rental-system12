# ✅ File app.py - Chuẩn hoá cho deploy trên Railway: login, phân quyền, quản lý thiết bị, QR/barcode, export CSV, đổi mật khẩu, upload ảnh
from fastapi import FastAPI, Request, Form, Query, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine, Column, String, Integer, Text
from pydantic import BaseModel
from uuid import uuid4
from typing import Optional
from dotenv import load_dotenv
import qrcode, io, os, csv, shutil
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import barcode
from barcode.writer import ImageWriter

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
if not DATABASE_URL or not SECRET_KEY:
    raise RuntimeError("Missing required environment variables: DATABASE_URL or SECRET_KEY")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory="templates")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

class UserDB(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String, default="user")

class AssetDB(Base):
    __tablename__ = "assets"
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    code = Column(String)
    category = Column(String)
    quantity = Column(Integer)
    description = Column(Text)
    image_path = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    if not db.query(UserDB).filter_by(username="admin").first():
        db.add(UserDB(id=str(uuid4()), username="admin", password="admin", role="admin"))
        db.commit()

class Asset(BaseModel):
    id: str
    name: str
    code: str
    category: str
    quantity: int
    description: Optional[str] = ""
    image_path: Optional[str] = None

class User(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"

@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path not in ("/login", "/logout", "/favicon.ico") and not request.url.path.startswith("/static"):
        if not request.session.get("user"):
            return RedirectResponse("/login")
    return await call_next(request)

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    user = db.query(UserDB).filter_by(username=username, password=password).first()
    db.close()
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Sai thông tin đăng nhập"})
    request.session["user"] = user.username
    request.session["role"] = user.role
    return RedirectResponse("/", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/users", response_class=HTMLResponse)
def list_users(request: Request):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập")
    db = SessionLocal()
    users = db.query(UserDB).all()
    db.close()
    return templates.TemplateResponse("users.html", {"request": request, "users": users})

@app.get("/change-password", response_class=HTMLResponse)
def change_password_form(request: Request):
    return templates.TemplateResponse("change_password.html", {"request": request})

@app.post("/change-password")
def change_password(request: Request, old_password: str = Form(...), new_password: str = Form(...)):
    username = request.session.get("user")
    db = SessionLocal()
    user = db.query(UserDB).filter_by(username=username, password=old_password).first()
    if not user:
        return templates.TemplateResponse("change_password.html", {"request": request, "error": "Mật khẩu cũ không đúng."})
    user.password = new_password
    db.commit()
    db.close()
    return RedirectResponse("/", status_code=302)

@app.get("/assets", response_class=HTMLResponse)
def list_assets(request: Request, keyword: Optional[str] = Query(None)):
    db = SessionLocal()
    query = db.query(AssetDB)
    if keyword:
        query = query.filter(AssetDB.name.ilike(f"%{keyword}%"))
    assets = query.all()
    db.close()
    return templates.TemplateResponse("assets.html", {"request": request, "assets": assets})

@app.get("/assets/add", response_class=HTMLResponse)
def add_asset_form(request: Request):
    return templates.TemplateResponse("asset_form.html", {"request": request})

@app.post("/assets/add")
def add_asset(request: Request, name: str = Form(...), code: str = Form(...), category: str = Form(...), quantity: int = Form(...), description: str = Form(""), image: UploadFile = File(None)):
    image_path = None
    if image:
        ext = os.path.splitext(image.filename)[1]
        filename = f"{uuid4()}{ext}"
        path = f"static/uploads/{filename}"
        with open(path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        image_path = f"/static/uploads/{filename}"
    db = SessionLocal()
    asset = AssetDB(id=str(uuid4()), name=name, code=code, category=category, quantity=quantity, description=description, image_path=image_path)
    db.add(asset)
    db.commit()
    db.close()
    return RedirectResponse("/assets", status_code=302)

@app.get("/assets/edit/{asset_id}", response_class=HTMLResponse)
def edit_asset_form(request: Request, asset_id: str):
    db = SessionLocal()
    asset = db.query(AssetDB).filter_by(id=asset_id).first()
    db.close()
    return templates.TemplateResponse("asset_form.html", {"request": request, "asset": asset})

@app.post("/assets/edit/{asset_id}")
def update_asset(request: Request, asset_id: str, name: str = Form(...), code: str = Form(...), category: str = Form(...), quantity: int = Form(...), description: str = Form(""), image: UploadFile = File(None)):
    db = SessionLocal()
    asset = db.query(AssetDB).filter_by(id=asset_id).first()
    if asset:
        asset.name = name
        asset.code = code
        asset.category = category
        asset.quantity = quantity
        asset.description = description
        if image:
            ext = os.path.splitext(image.filename)[1]
            filename = f"{uuid4()}{ext}"
            path = f"static/uploads/{filename}"
            with open(path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            asset.image_path = f"/static/uploads/{filename}"
        db.commit()
    db.close()
    return RedirectResponse("/assets", status_code=302)

@app.get("/assets/delete/{asset_id}")
def delete_asset(request: Request, asset_id: str):
    db = SessionLocal()
    asset = db.query(AssetDB).filter_by(id=asset_id).first()
    if asset:
        db.delete(asset)
        db.commit()
    db.close()
    return RedirectResponse("/assets", status_code=302)

@app.get("/assets/export")
def export_assets():
    db = SessionLocal()
    assets = db.query(AssetDB).all()
    db.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Tên", "Mã", "Danh mục", "Số lượng"])
    for a in assets:
        writer.writerow([a.id, a.name, a.code, a.category, a.quantity])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8")), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=assets.csv"})

@app.get("/assets/qr/{asset_id}")
def generate_qr(asset_id: str):
    img = qrcode.make(asset_id)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/assets/barcode/{asset_id}")
def generate_barcode(asset_id: str):
    code128 = barcode.get("code128", asset_id, writer=ImageWriter())
    buf = io.BytesIO()
    code128.write(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")
