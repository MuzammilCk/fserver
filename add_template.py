from app.database import SessionLocal
from app.models import MachineTemplate
import uuid

def add_new_template(machine_id, template_name, file_name, total_pages):
    db = SessionLocal()
    try:
        new_template = MachineTemplate(
            id=f"tpl_{uuid.uuid4().hex[:8]}",
            machine_id=machine_id,
            name=template_name,
            file_name=file_name,
            pages=total_pages
        )
        db.add(new_template)
        db.commit()
        print(f"✅ Successfully linked '{template_name}' to Machine {machine_id}!")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        db.close()
# Example: Adding a template to Kiosk-010
add_new_template(
    machine_id="Kiosk-010", 
    template_name="Hindustan PDF",  # You can change this name to whatever you want the user to see
    file_name="hindustanpdf.pdf",   # This must match the exact filename
    total_pages=1                   # Change this number if the PDF has more than 1 page!
)
