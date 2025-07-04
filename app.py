from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import boto3, uuid, os, logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your_secret_key")

# Logger Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- AWS Setup ----------------
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
sns = boto3.client('sns', region_name='us-east-1')
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

# DynamoDB Tables
doctor_table = dynamodb.Table("MedTrackDoctors")
patient_table = dynamodb.Table("MedTrackPatients")
appointment_table = dynamodb.Table("MedTrackAppointments")
prescription_table = dynamodb.Table("MedTrackPrescriptions")

# ---------------- DB Helper ----------------
def get_doctor_by_email(email):
    return doctor_table.get_item(Key={"email": email}).get("Item")

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        role = request.form.get("role")
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        gender = request.form.get("gender")
        password = request.form.get("password")

        table = doctor_table if role == "doctor" else patient_table
        if table.get_item(Key={"email": email}).get("Item"):
            flash("Email already registered.")
            return redirect(url_for("signup"))

        table.put_item(Item={
            "email": email,
            "name": name,
            "phone": phone,
            "gender": gender,
            "password": password,
            "role": role
        })

        session.update({"name": name, "email": email, "role": role})
        return redirect(url_for("doctor_dashboard" if role == "doctor" else "patient_dash"))

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        email = request.form.get("email")
        password = request.form.get("password")

        table = doctor_table if role == "doctor" else patient_table
        user = table.get_item(Key={"email": email}).get("Item")

        if not user or user["password"] != password:
            flash("Invalid credentials.")
            return redirect(url_for("login"))

        session.update({"name": user["name"], "email": user["email"], "role": role})
        return redirect(url_for("doctor_dashboard" if role == "doctor" else "patient_dash"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("index"))

@app.route("/doctor_dashboard")
def doctor_dashboard():
    if session.get("role") != "doctor": return redirect(url_for("login"))

    name = session.get("name")
    write_mode = request.args.get("write_mode") == "yes"
    show_all = request.args.get("show_all") == "yes"

    upcoming, completed, patient_list = [], [], []
    total = 0

    items = appointment_table.scan().get("Items", [])
    for item in items:
        if item.get("doctor") != name: continue
        total += 1
        if item.get("prescription"):
            completed.append(item)
        else:
            upcoming.append(item)
            patient_list.append(item["patient"])

    return render_template("doctor_dashboard.html", name=name, write_mode=write_mode,
                           upcoming=upcoming if show_all else upcoming[:3],
                           completed=completed if show_all else completed[:3],
                           total=total, patient_list=patient_list,
                           pending_appointments=upcoming[:3], completed_appointments=completed[:3],
                           total_appointments=(upcoming + completed)[:3],
                           pending_count=len(upcoming), completed_count=len(completed), total_count=total)

@app.route("/doctor_view_patients")
def doctor_view_patients():
    if session.get("role") != "doctor": return redirect(url_for("login"))

    name = session.get("name")
    items = appointment_table.scan().get("Items", [])
    patients = [item for item in items if item.get("doctor") == name and item.get("status") == "accepted"]

    return render_template("doctor_view_patients.html", name=name, patients=patients)

@app.route("/submit_prescription", methods=["POST"])
def submit_prescription():
    if session.get("role") != "doctor": return redirect(url_for("login"))

    doctor = session.get("name")
    patient = request.form["patient"]
    prescription = request.form["prescription"]

    prescription_table.put_item(Item={"doctor": doctor, "patient": patient, "prescription": prescription})

    for item in appointment_table.scan()["Items"]:
        if item.get("doctor") == doctor and item.get("patient") == patient and item.get("status") == "accepted":
            appointment_table.update_item(
                Key={"id": item["id"]},
                UpdateExpression="SET prescription = :val",
                ExpressionAttributeValues={":val": prescription})
            break

    return redirect(url_for("doctor_dashboard", prescription_success="yes"))

@app.route("/doctor_profile", methods=["GET", "POST"])
def doctor_profile():
    if session.get("role") != "doctor": return redirect(url_for("login"))

    email = session.get("email")

    if request.method == "POST":
        doctor_table.update_item(
            Key={"email": email},
            UpdateExpression="SET #n = :name, phone = :phone, gender = :gender, password = :pwd",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={
                ":name": request.form["name"],
                ":phone": request.form["phone"],
                ":gender": request.form["gender"],
                ":pwd": generate_password_hash(request.form["password"])
            })
        session["name"] = request.form["name"]
        flash("Profile updated successfully.")
        return redirect(url_for("doctor_profile"))

    doctor = doctor_table.get_item(Key={"email": email}).get("Item", {})
    return render_template("doctor_profile.html", user=doctor)

@app.route("/patient_dashboard", endpoint="patient_dash")
def patient_dashboard():
    if session.get("role") != "patient": return redirect(url_for("login"))
    name = session.get("name")

    show_all = request.args.get("show_all")
    prescription_success = request.args.get("prescription_success")

    appointments = appointment_table.scan()["Items"]
    prescriptions = prescription_table.scan()["Items"]

    upcoming = [a for a in appointments if a.get("patient") == name and a.get("status") == "accepted" and not a.get("prescription")]
    completed = [a for a in appointments if a.get("patient") == name and a.get("prescription")]
    patient_prescriptions = [p for p in prescriptions if p.get("patient") == name]

    return render_template("patient_dashboard.html", name=name,
                           upcoming=upcoming if show_all == "upcoming" else upcoming[:3],
                           completed=completed if show_all == "completed" else completed[:3],
                           prescriptions=patient_prescriptions if show_all == "prescriptions" else patient_prescriptions[:3],
                           upcoming_count=len(upcoming), completed_count=len(completed), prescription_count=len(patient_prescriptions),
                           upcoming_more=len(upcoming) > 3, completed_more=len(completed) > 3, prescriptions_more=len(patient_prescriptions) > 3,
                           prescription_success=prescription_success)

@app.route("/patient_profile", methods=["GET", "POST"])
def patient_profile():
    if session.get("role") != "patient": return redirect(url_for("login"))

    email = session.get("email")
    if request.method == "POST":
        patient_table.put_item(Item={
            "email": email,
            "name": request.form["name"],
            "phone": request.form["phone"],
            "gender": request.form["gender"],
            "password": generate_password_hash(request.form["password"]),
            "role": "patient"
        })
        flash("Profile updated.")
        return redirect(url_for("patient_profile"))

    patient = patient_table.get_item(Key={"email": email}).get("Item", {})
    return render_template("patient_profile.html", profile=patient)

@app.route("/book_appointment", methods=["GET", "POST"])
def book_appointment():
    if session.get("role") != "patient": return redirect(url_for("login"))

    name = session.get("name")
    if request.method == "POST":
        appointment = {
            "id": str(uuid.uuid4()),
            "patient": name,
            "doctor": request.form["doctor"],
            "date": request.form["date"],
            "time": request.form["time"],
            "problem": request.form["problem"],
            "status": "accepted",
            "prescription": ""
        }
        appointment_table.put_item(Item=appointment)

        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=f"New appointment booked with Dr. {appointment['doctor']} by {name} on {appointment['date']} at {appointment['time']}",
            Subject="New Appointment")

        return redirect(url_for("patient_dashboard"))

    doctors = doctor_table.scan()["Items"]
    return render_template("book_appointment.html", name=name, doctors=doctors)

@app.route("/contact")
def contact():
    return render_template("contact.html")

if __name__ == "__main__":
    app.run(debug=True)