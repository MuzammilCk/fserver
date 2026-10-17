import firebase_admin
from firebase_admin import credentials, messaging

# Initialize only once
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

def send_push_notification(fcm_token: str, title: str, body: str):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            token=fcm_token,
        )
        response = messaging.send(message)
        print(f"🚀 Successfully sent notification: {response}")
    except Exception as e:
        print(f"❌ FCM Error: {e}")
