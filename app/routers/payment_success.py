from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from app.database import get_db
from app.models import Session as SessionModel
from sqlalchemy.orm import Session
import logging

# ✅ IMPORT THE MQTT FUNCTION
from app.routers.sessions import push_mqtt_command, get_file_url

router = APIRouter()
logger = logging.getLogger("payment_success")

# --- Shared CSS Styles (HyNa Branding) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f4f4f9;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            color: #333;
            padding: 20px;
        }}
        .card {{
            background: white;
            padding: 40px 30px;
            border-radius: 20px;
            box-shadow: 0 10px 25px rgba(39, 10, 75, 0.15);
            text-align: center;
            max-width: 400px;
            width: 100%;
            margin-bottom: 20px;
            animation: popIn 0.5s ease-out;
        }}
        .icon-circle {{
            width: 80px;
            height: 80px;
            border-radius: 50%;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0 auto 20px auto;
            font-size: 40px;
            font-weight: bold;
        }}
        .success {{ background-color: #e0d4fc; color: #270a4b; }}
        .error   {{ background-color: #ffebee; color: #d32f2f; }}
        .pending {{ background-color: #fff3e0; color: #ef6c00; }}

        h1 {{ margin: 10px 0; font-size: 24px; color: #270a4b; }}
        p {{ color: #666; font-size: 16px; line-height: 1.5; margin-bottom: 30px; }}

        .btn {{
            background-color: #270a4b;
            color: white;
            padding: 12px 30px;
            border-radius: 50px;
            text-decoration: none;
            font-weight: bold;
            display: inline-block;
            transition: background 0.3s;
            cursor: pointer;
            width: 100%;
            box-sizing: border-box;
        }}
        .btn:hover {{ background-color: #3d146b; }}

        /* --- FEEDBACK FORM STYLES --- */
        .feedback-box {{
            background: #fff;
            width: 100%;
            max-width: 400px;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
        }}
        .feedback-header {{
            text-align: center;
            margin-bottom: 15px;
            font-weight: bold;
            color: #270a4b;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
        }}
        .feedback-form {{
            display: block;
            margin-top: 5px;
            text-align: left;
        }}
        .form-group {{ margin-bottom: 12px; }}
        label {{ display: block; font-size: 12px; font-weight: bold; margin-bottom: 4px; color: #555; }}
        input, textarea, select {{
            width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 8px;
            font-size: 14px; box-sizing: border-box;
        }}
        .submit-btn {{
            background: #6c5ce7;
            color: white; border: none; padding: 10px; width: 100%; border-radius: 8px;
            font-weight: bold; cursor: pointer; margin-top: 5px;
        }}

        @keyframes popIn {{
            0% {{ transform: scale(0.8); opacity: 0; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon-circle {status_class}">
            <span>{icon}</span>
        </div>
        <h1>{title}</h1>
        <p>{message}</p>
        <a href="#" class="btn" onclick="window.close()">Close Window</a>
    </div>

    <div class="feedback-box">
        <div class="feedback-header">
            Rate Your Experience
        </div>

        <form class="feedback-form" id="feedbackForm" action="https://formcarry.com/s/RAqQNCV997Q" method="POST" accept-charset="UTF-8">
            <input type="hidden" name="_gotcha">

            <div class="form-group">
                <label>Issue Type</label>
                <select name="type">
                    <option value="Review">⭐ Positive Review</option>
                    <option value="Complaint">⚠️ Complaint / Issue</option>
                    <option value="Suggestion">💡 Suggestion</option>
                </select>
            </div>

            <div class="form-group">
                <label>Message</label>
                <textarea name="message" rows="3" placeholder="Tell us what happened..." required></textarea>
            </div>

            <div class="form-group">
                <label>Email (Optional)</label>
                <input type="email" name="email" placeholder="For us to contact you">
            </div>

            <input type="hidden" name="session_id" value="{session_id}">

            <button type="submit" class="submit-btn">Submit Feedback</button>
        </form>
    </div>

    {extra_script}

</body>
</html>
"""

@router.get("/payment-success", response_class=HTMLResponse)
async def payment_success(request: Request, db: Session = Depends(get_db)): 
    session_id = request.query_params.get("session_id") or "unknown"

    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session:
        return HTML_TEMPLATE.format(
            title="Session Not Found",
            status_class="error",
            icon="?",
            message="We could not find the session details.",
            extra_script="",
            session_id=session_id
        )

    # ✅ ZERO POLLING: Just read the local database!
    if session.payment_status == "success":
        
        # 🚀 ADDED: SEND MQTT TRIGGER HERE!
        # Only send if it hasn't already started printing
        if session.status not in ["printing", "completed", "failed"]:
            session.status = "ready_to_print"
            db.commit()
            
            try:
                push_mqtt_command(
                    machine_id=session.machine_id,
                    action="start_print",
                    session_data={
                        "id": session.id,
                        "file_url": get_file_url(session.file_path),
                        "start_page": session.start_page,
                        "end_page": session.end_page,
                        "copies": session.copies,
                        "color": session.color,
                        "duplex": getattr(session, "duplex", False),
                        "orientation": session.orientation
                    }
                )
            except Exception as e:
                logger.error(f"❌ Failed to push MQTT on payment success: {e}")
        # ---------------------------------------------

        return HTML_TEMPLATE.format(
            title="Payment Successful!",
            status_class="success",
            icon="✔",
            message="Your payment has been received.<br><b>Look at the HyNa Kiosk screen</b> to see your print progress.",
            extra_script="",
            session_id=session_id
        )
    elif session.payment_status == "failed":
        return HTML_TEMPLATE.format(
            title="Payment Failed",
            status_class="error",
            icon="✖",
            message="The payment could not be completed. Please try again.",
            extra_script="",
            session_id=session_id
        )
    else:
        # Still Pending - Reload the page to check the database again
        return HTML_TEMPLATE.format(
            title="Payment Pending",
            status_class="pending",
            icon="⏳",
            message="We are still waiting for confirmation from the bank. Please wait a moment...",
            extra_script="<script>setTimeout(() => location.reload(), 3000);</script>",
            session_id=session_id
        )
