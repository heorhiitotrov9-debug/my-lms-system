import os
import io
import base64
import random
from typing import List
from fastapi import FastAPI, Request, Form, Response, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
import qrcode

# --- Налаштування Бази Даних ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lms.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Для PostgreSQL не використовуємо check_same_thread
if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Моделі БД ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_index=True, primary_key=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)  # admin, teacher, student
    name = Column(String)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    code = Column(String)
    course = Column(String)

class Test(Base):
    __tablename__ = "tests"
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("users.id"))
    subject = Column(String)
    title = Column(String)
    questions = Column(JSON)  # Зберігаємо список питань як JSON

class QRCodeModel(Base):
    __tablename__ = "qr_codes"
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer)
    test_id = Column(Integer)
    test_title = Column(String)
    subject = Column(String)
    qr_base64 = Column(Text)

class TestResult(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True)
    student_name = Column(String)
    subject = Column(String)
    test_title = Column(String)
    score = Column(String)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Захищена LMS система")
templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Автоматична ініціалізація дефолтного адміна
def init_db():
    db = SessionLocal()
    if not db.query(User).filter_by(role="admin").first():
        admin = User(email="admin@lms.com", password="123", role="admin", name="Головний Адміністратор")
        teacher = User(email="teacher@lms.com", password="123", role="teacher", name="Петренко Іван Олексійович")
        db.add_all([admin, teacher])
        db.commit()
    db.close()

init_db()

def get_current_user(request: Request, db: Session):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id)).first()

# --- Маршрути Входу ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(url=f"/{user.role}", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login")
async def login(request: Request, response: Response, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email, User.password == password).first()
    if not user:
        return templates.TemplateResponse(request, "login.html", {"error": "Невірний логін або пароль"})
    res = RedirectResponse(url=f"/{user.role}", status_code=303)
    res.set_cookie(key="user_id", value=str(user.id))
    return res

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/", status_code=303)
    res.delete_cookie("user_id")
    return res

# --- Адмін-панель ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return RedirectResponse(url="/", status_code=303)
    
    teachers = db.query(User).filter(User.role == "teacher").all()
    students = db.query(User).filter(User.role == "student").all()
    groups = db.query(Group).all()
    results = db.query(TestResult).all()
    
    groups_dict = {g.id: g.code for g in groups}
    for s in students:
        s.group_name = groups_dict.get(s.group_id, "Без групи")
        
    return templates.TemplateResponse(request, "admin.html", {"user": user, "teachers": teachers, "students": students, "groups": groups, "results": results})

@app.post("/admin/add-teacher")
async def add_teacher(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    db.add(User(name=name, email=email, password=password, role="teacher"))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/add-group")
async def add_group(request: Request, code: str = Form(...), course: str = Form(...), db: Session = Depends(get_db)):
    db.add(Group(code=code, course=course))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin/delete-group/{group_id}")
async def delete_group(request: Request, group_id: int, db: Session = Depends(get_db)):
    db.query(Group).filter(Group.id == group_id).delete()
    db.query(User).filter(User.group_id == group_id).update({"group_id": None})
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/add-student")
async def add_student(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...), group_id: int = Form(...), db: Session = Depends(get_db)):
    db.add(User(name=name, email=email, password=password, role="student", group_id=group_id))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin/delete-user/{user_id}")
async def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    db.query(User).filter(User.id == user_id, User.role != "admin").delete()
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

# --- Панель Викладача (Мультипитання) ---

@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "teacher":
        return RedirectResponse(url="/", status_code=303)
    
    my_tests = db.query(Test).filter(Test.teacher_id == user.id).all()
    my_qrs = db.query(QRCodeModel).filter(QRCodeModel.teacher_id == user.id).all()
    my_test_titles = [t.title for t in my_tests]
    my_results = db.query(TestResult).filter(TestResult.test_title.in_(my_test_titles)).all()
    
    return templates.TemplateResponse(request, "teacher.html", {"user": user, "tests": my_tests, "qr_codes": my_qrs, "results": my_results})

@app.post("/teacher/add-test")
async def add_test(
    request: Request, 
    subject: str = Form(...), 
    title: str = Form(...),
    question_text: List[str] = Form(...),
    option_a: List[str] = Form(...),
    option_b: List[str] = Form(...),
    option_c: List[str] = Form(...),
    option_d: List[str] = Form(...),
    correct_option: List[str] = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != "teacher":
        return RedirectResponse(url="/", status_code=303)
    
    questions_data = []
    for i in range(len(question_text)):
        questions_data.append({
            "id": i + 1,
            "text": question_text[i],
            "options": {"A": option_a[i], "B": option_b[i], "C": option_c[i], "D": option_d[i]},
            "correct": correct_option[i]
        })
        
    new_test = Test(teacher_id=user.id, subject=subject, title=title, questions=questions_data)
    db.add(new_test)
    db.commit()
    return RedirectResponse(url="/teacher", status_code=303)

@app.get("/teacher/generate-qr/{test_id}")
async def generate_qr(request: Request, test_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        return RedirectResponse(url="/teacher", status_code=303)
    
    test_url = f"https://{request.headers.get('host')}/student/take-test/{test_id}"
    img = qrcode.make(test_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    qr = db.query(QRCodeModel).filter(QRCodeModel.test_id == test_id, QRCodeModel.teacher_id == user.id).first()
    if not qr:
        db.add(QRCodeModel(teacher_id=user.id, test_id=test_id, test_title=test.title, subject=test.subject, qr_base64=img_str))
        db.commit()

    return RedirectResponse(url="/teacher", status_code=303)

@app.get("/teacher/delete-test/{test_id}")
async def delete_test(request: Request, test_id: int, db: Session = Depends(get_db)):
    db.query(Test).filter(Test.id == test_id).delete()
    db.query(QRCodeModel).filter(QRCodeModel.test_id == test_id).delete()
    db.commit()
    return RedirectResponse(url="/teacher", status_code=303)

# --- Курсант ---

@app.get("/student", response_class=HTMLResponse)
async def student_page(request: Request, submitted: bool = False, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "student":
        return RedirectResponse(url="/", status_code=303)
    
    group = db.query(Group).filter(Group.id == user.group_id).first()
    tests = db.query(Test).all()
    return templates.TemplateResponse(request, "student.html", {"user": user, "group_name": group.code if group else "Без групи", "tests": tests, "submitted": submitted})

@app.get("/student/take-test/{test_id}", response_class=HTMLResponse)
async def take_test(request: Request, test_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        return RedirectResponse(url="/student", status_code=303)
    
    shuffled_questions = []
    for q in test.questions:
        opts = list(q["options"].items())
        random.shuffle(opts)
        shuffled_questions.append({"id": q["id"], "text": q["text"], "shuffled_options": opts})
        
    return templates.TemplateResponse(request, "take_test.html", {"user": user, "test": test, "questions": shuffled_questions})

@app.post("/student/submit-test/{test_id}")
async def submit_test(request: Request, test_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    form_data = await request.form()
    test = db.query(Test).filter(Test.id == test_id).first()
    
    if test:
        correct = sum(1 for q in test.questions if form_data.get(f"question_{q['id']}") == q["correct"])
        score = f"{int((correct / len(test.questions)) * 100)}%"
        db.add(TestResult(student_name=user.name, subject=test.subject, test_title=test.title, score=score))
        db.commit()

    return RedirectResponse(url="/student?submitted=true", status_code=303)
