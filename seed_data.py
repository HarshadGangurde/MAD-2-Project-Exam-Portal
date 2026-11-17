"""
Seed script to populate the EMP database with sample data.
Run this after starting the app at least once (so db tables are created).
It asks for confirmation first; pass --yes to skip the prompt.
"""
import os
import sys
from datetime import date, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))
from app import create_app

from models import (db, User, Course, Exam, Rubric, Slot, Booking, Evaluation, EvaluationScore,
                    EvaluationHistory, Notification, ExportJob, exam_examiners)

app = create_app()

with app.app_context():
    if "--yes" not in sys.argv:
        answer = input("This DELETES all courses, exams, slots, bookings and non-admin users. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            sys.exit("Aborted - nothing was changed.")

    # Clear existing data (except admin). Order matters: foreign keys are enforced, children go first.
    print("Clearing existing data...")
    EvaluationScore.query.delete()
    EvaluationHistory.query.delete()
    Evaluation.query.delete()
    Notification.query.delete()
    ExportJob.query.delete()
    Booking.query.delete()
    Slot.query.delete()
    Rubric.query.delete()
    db.session.execute(exam_examiners.delete())
    Exam.query.delete()
    Course.query.delete()
    User.query.filter(User.role != 'admin').delete()
    db.session.commit()
    
    print("Creating courses...")
    courses = [
        Course(code="CS101", name="Introduction to Programming", description="Basic programming concepts using Python", status="Active"),
        Course(code="CS201", name="Data Structures", description="Arrays, linked lists, trees, graphs", status="Active"),
        Course(code="CS301", name="Database Management Systems", description="SQL, normalization, transactions", status="Active"),
        Course(code="CS401", name="Web Development", description="HTML, CSS, JavaScript, Flask, Vue", status="Active"),
    ]
    db.session.add_all(courses)
    db.session.commit()
    
    print("Creating examiners...")
    examiners = []
    examiner_data = [
        ("Dr. Rajesh Kumar", "rajesh@university.edu", "+91-9876543210", "Computer Science"),
        ("Prof. Anita Sharma", "anita@university.edu", "+91-9876543211", "Computer Science"),
        ("Dr. Vikram Singh", "vikram@university.edu", "+91-9876543212", "Information Technology"),
        ("Prof. Priya Patel", "priya@university.edu", "+91-9876543213", "Computer Science"),
    ]
    for name, email, phone, dept in examiner_data:
        examiner = User(email=email, name=name, role="examiner", phone=phone, department=dept, status="Active")
        examiner.set_password("examiner123")
        examiners.append(examiner)
    db.session.add_all(examiners)
    db.session.commit()
    
    print("Creating students...")
    students = []
    student_data = [
        ("Rahul Verma", "rahul.verma@student.edu", "+91-9123456780", "Computer Science"),
        ("Priya Singh", "priya.singh@student.edu", "+91-9123456781", "Computer Science"),
        ("Amit Patel", "amit.patel@student.edu", "+91-9123456782", "Information Technology"),
        ("Sneha Gupta", "sneha.gupta@student.edu", "+91-9123456783", "Computer Science"),
        ("Rohan Sharma", "rohan.sharma@student.edu", "+91-9123456784", "Computer Science"),
        ("Anjali Reddy", "anjali.reddy@student.edu", "+91-9123456785", "Information Technology"),
        ("Karan Mehta", "karan.mehta@student.edu", "+91-9123456786", "Computer Science"),
        ("Neha Joshi", "neha.joshi@student.edu", "+91-9123456787", "Computer Science"),
    ]
    for name, email, phone, dept in student_data:
        student = User(email=email, name=name, role="student", phone=phone, department=dept, status="Active")
        student.set_password("student123")
        students.append(student)
    db.session.add_all(students)
    db.session.commit()
    
    print("Creating examinations...")
    today = date.today()
    exams = [
        Exam(
            course_id=courses[0].id,
            name="Python Programming Viva",
            type="Viva",
            duration=20,
            max_marks=25,
            instructions="Be prepared to explain basic Python concepts, data types, and control structures.",
            slot_start=today - timedelta(days=10),
            slot_end=today + timedelta(days=5),
            booking_start=today - timedelta(days=5),
            booking_end=today + timedelta(days=10),
            status="Booking Open"
        ),
        Exam(
            course_id=courses[1].id,
            name="Data Structures Practical",
            type="Practical",
            duration=60,
            max_marks=50,
            instructions="Implement linked list operations and tree traversal algorithms.",
            slot_start=today - timedelta(days=7),
            slot_end=today + timedelta(days=7),
            booking_start=today - timedelta(days=3),
            booking_end=today + timedelta(days=12),
            status="Booking Open"
        ),
        Exam(
            course_id=courses[2].id,
            name="DBMS Project Demo",
            type="Project Demo",
            duration=30,
            max_marks=40,
            instructions="Demonstrate your database project with working queries and explain schema design.",
            slot_start=today - timedelta(days=5),
            slot_end=today + timedelta(days=10),
            booking_start=today - timedelta(days=2),
            booking_end=today + timedelta(days=15),
            status="Booking Open"
        ),
        Exam(
            course_id=courses[3].id,
            name="Web Development Assessment",
            type="Assessment",
            duration=45,
            max_marks=30,
            instructions="Build a simple web application using Flask and Vue.js.",
            slot_start=today + timedelta(days=5),
            slot_end=today + timedelta(days=20),
            booking_start=today + timedelta(days=10),
            booking_end=today + timedelta(days=25),
            status="Slot Creation"
        ),
    ]
    db.session.add_all(exams)
    db.session.commit()
    
    # Assign examiners to exams
    exams[0].examiners = [examiners[0], examiners[1]]
    exams[1].examiners = [examiners[1], examiners[2]]
    exams[2].examiners = [examiners[2], examiners[3]]
    exams[3].examiners = [examiners[0], examiners[3]]
    db.session.commit()
    
    print("Creating rubrics...")
    rubrics_data = [
        # Python Viva rubrics
        (exams[0].id, "Understanding of Concepts", 8, 1.0, "Clear explanation of Python fundamentals"),
        (exams[0].id, "Code Reading", 7, 0.8, "Ability to read and understand code snippets"),
        (exams[0].id, "Problem Solving", 10, 1.2, "Logical approach to solving problems"),
        
        # Data Structures Practical rubrics
        (exams[1].id, "Code Implementation", 20, 1.0, "Correctness and efficiency of implementation"),
        (exams[1].id, "Code Quality", 15, 0.8, "Clean code, proper naming, comments"),
        (exams[1].id, "Testing", 15, 0.7, "Test cases and edge case handling"),
        
        # DBMS Project rubrics
        (exams[2].id, "Schema Design", 15, 1.0, "Proper normalization and relationships"),
        (exams[2].id, "Query Writing", 15, 1.0, "Correct and optimized SQL queries"),
        (exams[2].id, "Presentation", 10, 0.6, "Clear explanation and demonstration"),
        
        # Web Dev Assessment rubrics
        (exams[3].id, "Frontend Implementation", 12, 1.0, "Vue.js components and UI design"),
        (exams[3].id, "Backend Implementation", 12, 1.0, "Flask API and routing"),
        (exams[3].id, "Functionality", 6, 0.5, "Working features and error handling"),
    ]
    rubrics = [Rubric(exam_id=eid, criterion=c, max_marks=m, weightage=w, description=d) 
               for eid, c, m, w, d in rubrics_data]
    db.session.add_all(rubrics)
    db.session.commit()
    
    print("Creating examination slots...")
    slots = []
    # Python Viva slots (Exam 1)
    for i in range(4):
        slot_date = today + timedelta(days=i+1)
        slots.append(Slot(
            exam_id=exams[0].id,
            examiner_id=examiners[i % 2].id,
            date=slot_date,
            start_time=time(10, 0),
            end_time=time(10, 20),
            capacity=2,
            available_seats=2,
            status="Available",
            meeting_link=f"https://meet.google.com/python-viva-{i+1}"
        ))
    
    # Data Structures Practical slots (Exam 2)
    for i in range(3):
        slot_date = today + timedelta(days=i+2)
        slots.append(Slot(
            exam_id=exams[1].id,
            examiner_id=examiners[(i % 2) + 1].id,
            date=slot_date,
            start_time=time(14, 0),
            end_time=time(15, 0),
            capacity=1,
            available_seats=1,
            status="Available",
            meeting_link=f"https://meet.google.com/ds-practical-{i+1}"
        ))
    
    # DBMS Project slots (Exam 3)
    for i in range(3):
        slot_date = today + timedelta(days=i+3)
        slots.append(Slot(
            exam_id=exams[2].id,
            examiner_id=examiners[(i % 2) + 2].id,
            date=slot_date,
            start_time=time(11, 0),
            end_time=time(11, 30),
            capacity=2,
            available_seats=2,
            status="Available",
            meeting_link=f"https://meet.google.com/dbms-demo-{i+1}"
        ))
    
    db.session.add_all(slots)
    db.session.commit()
    
    print("Creating sample bookings...")
    # Book some slots for students
    bookings = [
        Booking(student_id=students[0].id, slot_id=slots[0].id, exam_id=exams[0].id, status="Booked"),
        Booking(student_id=students[1].id, slot_id=slots[0].id, exam_id=exams[0].id, status="Booked"),
        Booking(student_id=students[2].id, slot_id=slots[1].id, exam_id=exams[0].id, status="Booked"),
        Booking(student_id=students[3].id, slot_id=slots[4].id, exam_id=exams[1].id, status="Booked"),
        Booking(student_id=students[4].id, slot_id=slots[7].id, exam_id=exams[2].id, status="Booked"),
    ]
    db.session.add_all(bookings)
    
    # Update slot available seats
    slots[0].available_seats = 0
    slots[0].status = "Full"
    slots[1].available_seats = 1
    slots[4].available_seats = 0
    slots[7].available_seats = 1
    
    db.session.commit()
    
    print("Creating sample evaluations...")
    # Add evaluation for one completed booking
    past_booking = bookings[0]
    past_booking.status = "Completed"
    
    evaluation = Evaluation(
        booking_id=past_booking.id,
        student_id=past_booking.student_id,
        examiner_id=examiners[0].id,
        total_marks=21.5,
        remarks="Good understanding of concepts. Needs improvement in problem-solving speed.",
        status="Evaluated"
    )
    db.session.add(evaluation)
    db.session.flush()  # Get evaluation.id
    
    # Add evaluation scores
    exam_rubrics = Rubric.query.filter_by(exam_id=exams[0].id).all()
    scores = [
        EvaluationScore(evaluation_id=evaluation.id, rubric_id=exam_rubrics[0].id, marks=7.0),
        EvaluationScore(evaluation_id=evaluation.id, rubric_id=exam_rubrics[1].id, marks=5.5),
        EvaluationScore(evaluation_id=evaluation.id, rubric_id=exam_rubrics[2].id, marks=9.0),
    ]
    db.session.add_all(scores)
    db.session.commit()
    
    print("\n" + "="*50)
    print("[SUCCESS] Database seeded successfully!")
    print("="*50)
    print("\nLogin credentials:")
    print("\n[ADMIN]:")
    print("   Email: admin@gmail.com")
    print("   Password: admin@123")
    print("\n[EXAMINER] (any of these):")
    print("   Email: rajesh@university.edu")
    print("   Email: anita@university.edu")
    print("   Email: vikram@university.edu")
    print("   Email: priya@university.edu")
    print("   Password: examiner123")
    print("\n[STUDENT] (any of these):")
    print("   Email: rahul.verma@student.edu")
    print("   Email: priya.singh@student.edu")
    print("   Email: amit.patel@student.edu")
    print("   Email: sneha.gupta@student.edu")
    print("   Password: student123")
    print("\n[SUMMARY]:")
    print(f"   - {len(courses)} Courses")
    print(f"   - {len(exams)} Examinations")
    print(f"   - {len(examiners)} Examiners")
    print(f"   - {len(students)} Students")
    print(f"   - {len(slots)} Examination Slots")
    print(f"   - {len(bookings)} Bookings")
    print(f"   - 1 Sample Evaluation")
    print("="*50 + "\n")
