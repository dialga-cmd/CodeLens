import firebase_admin
from firebase_admin import auth, credentials
from fastapi import HTTPException, Security, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import os
import json

security = HTTPBearer(auto_error=False)

def initialize_firebase():
    if not firebase_admin._apps:
        # Prefer a service account JSON provided directly in an env var (sa JSON string)
        sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        if sa_json:
            try:
                sa_dict = json.loads(sa_json)
                cred = credentials.Certificate(sa_dict)
                firebase_admin.initialize_app(cred)
                return
            except Exception:
                print("Warning: Could not parse FIREBASE_SERVICE_ACCOUNT JSON; falling back to file path")

        # Expecting a path to the service account JSON file
        cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            return

        # Fallback to default service account file if it exists (local dev)
        default_cred_path = "/home/dialgga/CodeLens/server/serviceAccountKey.json"
        if os.path.exists(default_cred_path):
            cred = credentials.Certificate(default_cred_path)
            firebase_admin.initialize_app(cred)
            return

        # Fallback to default credentials (useful for some environments)
        try:
            firebase_admin.initialize_app()
        except Exception:
            print("Warning: Firebase Admin not initialized. Auth verification will fail.")

async def verify_token(
    res: HTTPAuthorizationCredentials = Security(security),
    token: str = Query(default=None)
):
    """
    Verifies the Firebase ID token passed in the Authorization header or query param.
    """
    # Prefer header, fallback to query param (for EventSource/SSE)
    id_token = (res.credentials if res else None) or token
    
    if not id_token:
        raise HTTPException(status_code=401, detail="Authentication token missing")
        
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication credentials: {str(e)}"
        )

