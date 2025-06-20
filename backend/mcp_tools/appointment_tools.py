# backend/mcp_tools/appointment_tools.py

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
import pytz
import logging

from backend.models import Appointment, Doctor, Patient, DoctorAvailability
from backend.services.google_calendar import create_event
from backend.services.email_service import send_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

class ToolException(Exception):
    """Custom exception for tool-related errors."""
    pass

async def book_appointment(
    db: Session,
    patient_email: str,
    doctor_email: str,
    appointment_time_str: str,
    reason: Optional[str] = None
) -> dict:
    """
    Books an appointment, and crucially, marks the availability slot as booked.
    """
    result = {
        "status": "success",
        "message": "",
        "appointment_id": None,
        "email_status": "not attempted",
        "calendar_event_link": "not attempted"
    }

    try:
        naive_appointment_time = datetime.strptime(appointment_time_str, "%Y-%m-%d %H:%M:%S")
        appointment_time = IST.localize(naive_appointment_time)
        end_time = appointment_time + timedelta(minutes=30)

        doctor = db.query(Doctor).filter(Doctor.email == doctor_email).first()
        if not doctor:
            result["status"] = "error"
            result["message"] = f"Doctor with email {doctor_email} not found."
            return result
            
        availability_slot = db.query(DoctorAvailability).filter(
            DoctorAvailability.doctor_id == doctor.id,
            DoctorAvailability.start_time == naive_appointment_time
        ).first()

        if not availability_slot:
            return {"status": "error", "message": f"The requested time slot {appointment_time_str} is not a valid availability for Dr. {doctor.name}."}
        
        if availability_slot.is_booked:
             return {"status": "error", "message": f"Sorry, the time slot {appointment_time_str} for Dr. {doctor.name} has already been booked."}

        availability_slot.is_booked = True

        patient = db.query(Patient).filter(Patient.email == patient_email).first()
        if not patient:
            logger.warning(f"Patient with email {patient_email} not found. Creating a new patient.")
            patient = Patient(name=patient_email.split('@')[0], email=patient_email)
            db.add(patient)

        appointment = Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            appointment_time=appointment_time,
            reason=reason,
            status="scheduled"
        )
        db.add(appointment)
        
        db.commit()

        db.refresh(appointment)
        logger.info(f"Appointment created in DB with ID: {appointment.id} and availability slot updated.")
        result["appointment_id"] = appointment.id
        result["message"] = "Appointment successfully created in database."

    except Exception as e:
        logger.error(f"An error occurred during DB operation in book_appointment: {e}", exc_info=True)
        db.rollback()
        result["status"] = "error"
        result["message"] = f"Database error: {e}"
        return result

    try:
        email_subject = "Your Appointment Confirmation"
        email_body = f"Dear {patient.name},\n\nYour appointment with Dr. {doctor.name} on {appointment_time.strftime('%Y-%m-%d at %H:%M %Z')} is confirmed."
        if await send_email(patient.email, email_subject, email_body):
            result["email_status"] = "Email sent successfully."
        else:
            result["email_status"] = "Email sending failed."
    except Exception as e:
        result["email_status"] = f"An unexpected error occurred during email sending: {e}"
        
    try:
        event_summary = f"Appointment: {patient.name} with Dr. {doctor.name}"
        event_description = f"Reason: {reason or 'N/A'}"
        calendar_link = await create_event(
            summary=event_summary, description=event_description, start_time=appointment_time,
            end_time=end_time, attendees=[patient.email, doctor.email]
        )
        result["calendar_event_link"] = calendar_link or "Failed to create calendar event."
    except Exception as e:
        result["calendar_event_link"] = f"An unexpected error occurred during calendar event creation: {e}"

    full_message = f"Appointment created with ID {result['appointment_id']}. Email status: {result['email_status']}. Calendar status: {result['calendar_event_link']}"
    result["message"] = full_message

    return result