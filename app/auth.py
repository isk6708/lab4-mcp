"""Login and the get_current_user dependency that protects /secure routes."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db import UserDB, get_db
from app.tokens import InvalidToken, decode_access_token

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# tokenUrl makes Swagger UI (/docs) show an "Authorize" button that logs in through POST /token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def authenticate(db: Session, username: str, password: str):
    """Return the active user if the password matches, else None."""
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if not user or not user.is_active or not pwd_context.verify(password, user.hashed_password):
        return None
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> UserDB:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                 detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        username = decode_access_token(token)
    except InvalidToken as exc:
        unauthorized.detail = str(exc)
        raise unauthorized from exc
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if not user or not user.is_active:
        raise unauthorized
    return user
