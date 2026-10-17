import firebase_admin
from firebase_admin import credentials, messaging
from pathlib import Path
import os

# Initialize Firebase Admin (Ensure the path to your JSON is correct)
# If already initialized in another file, you don't need this block again
base_path = Path(__file__).resolve().parent.parent.parent
cred_path = os.path.join(base_path, "serviceAccountKey.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

def send_push_notification(token: str, title: str, body: str, data: dict = None):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=token,
        )
        response = messaging.send(message)
        print(f"✅ FCM Sent Successfully: {response}")
        return response
    except Exception as e:
        print(f"❌ FCM Error: {e}")
        return None
