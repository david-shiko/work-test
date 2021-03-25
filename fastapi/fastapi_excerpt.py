from typing import Optional
from httpx import AsyncClient
from fastapi import APIRouter, Depends, Query, responses, Header
from sqlalchemy.orm import Session
from app import send_email, schemas, config, crud, services
from app.database import get_db
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import datetime, timedelta
from secrets import token_urlsafe as secrets_token_urlsafe


router = APIRouter()


@router.post("/register", status_code=201)
def register(
        user_create: schemas.UserCreate,    
        db: Session = Depends(get_db)):
    try:
        if not crud.read_user_by(db, column='email', value=user_create.email):
            registration_token = secrets_token_urlsafe(nbytes=config.URL_TOKEN_SIZE)  # Create url-safe secret token
            send_email.confirm_link(token=registration_token,
                                    endpoint='http://127.0.0.1:8000/confirm-email',
                                    email=user_create.email)
            return crud.create_user(db=db, user = {'access_token': registration_token,
                                                   'login': user_create.login,
                                                   'hashed_password': config.pwd_context.hash(user_create.password)}, )
        else:
            raise config.fastapi_http_errors['email_already_exists_409']
    except Exception as error:
        services.error_handler(error)


@router.get("/confirm-email", status_code=303, )
def confirm_email(token: str = Query(...,
                                     min_length=64, max_length=256,
                                     description='A confirmation link consist from endpoint'
                                                '(url where to send a token) and a token parameter itself.',
                                     example='1GS7nb4wd55LKyF-nyY92FEQyS3zWe4UaGX4Nm'),
                  db: Session = Depends(get_db), ):
    try:
        user = crud.read_user_by(db=db, column='current_token', value=token)
        if user.created_datetime + timedelta(minutes=60 * 24 * 2) > datetime.now():
            jwt_token = services.create_access_token(email=user.email)  # Create jwt token instead of url_safe token
            crud.update_user(db=db, user=user, new_user={'is-active': True, 'current_token': jwt_token})
            return responses.RedirectResponse(url='')  # TODO add endpoint to redirect
        else:
            raise config.fastapi_http_errors['link_expired_410']
    except Exception as error:
        services.error_handler(error)


@router.post("/login", status_code=201)
def grant_token(
        form_data: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db)):
    try:
        email = form_data.username
        user = crud.read_user_by(db=db, column='email', value=email)
        if user and services.verify_password(form_data.password, user.hashed_password):
            access_token = services.create_access_token(email=email)
            crud.update_user(db=db, user=user, new_user={'current_token': access_token})
            return {"access_token": access_token, "token_type": "bearer"}
        else:
            raise config.fastapi_http_errors['wrong_token_403']
    except Exception as error:
        services.error_handler(error)


@router.post("/restore-password", status_code=201)
def user_restore_password(
        email: schemas.Email = Query(...,
                                     description="Email to send link with token",
                                     example='dsb321mp@gmail.com'),
        db: Session = Depends(get_db), ):
    try:
        if user := crud.read_user_by(db, column='email', value=email):
            send_email.restore_password(
                token=user.reset_token, endpoint='http://127.0.0.1:8000/restore-password', email=email)
            return 'Success. Link with a restore link was sent to a specified email'
        else:
            raise config.fastapi_http_errors['user_not_found_404']  # SECURITY preserve email anonymity ?
    except Exception as error:
        services.error_handler(error)


@router.get("/restore-password", status_code=201)
def user_restore_password(
        token: str = Query(...,
                           description="url_safe_token that was sent to the email.",
                           example='nuLouGT8Tl6adEtiHBlaDg2bKQ3G5Q6pD7rrqfL71vz4W3ieoSZn_r8jqSMWgJN8'),
        db: Session = Depends(get_db)):
    try:
        if user := crud.read_user_by(db, column='reset_token', value=token):
            crud.update_user(db=db, user=user, new_user=schemas.UserUpdate(
                {'reset_token': secrets_token_urlsafe(config.URL_TOKEN_SIZE)}))
            # TODO create new pass
        else:
            raise config.fastapi_http_errors['wrong_token_403']
    except Exception as error:
        services.error_handler(error)


@router.post("/refresh-token")  # Not in use
def refresh_token(
        token: str = Header(...),
        db: Session = Depends(get_db)):
    try:
        user = services.get_user_by_token(db=db, token=token)
        access_token = services.create_access_token(email=user.email)
        crud.update_user(db=db, user=user, new_user={'current_token': access_token})
        return access_token  # Return new token?
    except Exception as error:
        services.error_handler(error)


@router.post("/logout")
def logout(token: str = Header(...), db: Session = Depends(get_db)):
    try:
        if user := services.get_user_by_token(db=db, token=token):
            crud.update_user(db=db, user=user, new_user={'current_token': None})
            return f'Successfully logout user_id {user.id}'
    except Exception as error:
        services.error_handler(error)


@router.get("/google_oauth_consent_screen", status_code=200, description='redirect user to google consent screen')
async def google_oauth():
    return responses.RedirectResponse(
        url=f'https://accounts.google.com/o/oauth2/v2/auth?'  # default
            f'client_id={config.google_oauth_client_id}&'
            f'nonce={secrets_token_urlsafe(16)}&'  # Any random value
            f'response_type=code&'  # default
            f'redirect_uri={config.google_oauth_redirect_uri}&'  # callback address
            f'scope=openid%20email')


@router.get("/google_oauth_check_token", status_code=200)
async def google_oauth(
        db: Session = Depends(get_db),
        code: Optional[str] = Query(None,
                                    description="Code that google sends as response (to redirect_uri) for the request. "
                                                "App must exchange this code on the access token (of user info?)")):
    try:
        async with AsyncClient() as async_client:  # See https://www.python-httpx.org/async/#making-requests
            response = await async_client.post("https://oauth2.googleapis.com/token",  # Make asynchronous post request
                                               data={"code": code,  # AA code from url
                                                     "client_id": config.google_oauth_client_id,
                                                     "client_secret": config.google_oauth_secret,
                                                     "redirect_uri": config.google_oauth_redirect_uri,
                                                     "grant_type": "authorization_code", })  # default
            response = response.json()  # -Convert string response to python dict (initial request is coroutine)
            user_email = services.decode_jwt_rsa(token=response['id_token'], access_token=response['access_token'])
            access_token = services.create_access_token(email=user_email)  # access_token in the token isn't encrypted!
            if user := crud.read_user_by(db=db, column='email', value=user_email):
                crud.update_user(db=db, user=user, new_user={'current_token': access_token})
            else:
                crud.create_user(db=db, user={'email': user_email,
                                       'hashed_password': access_token,  # User may login via password (token) also
                                       'is_active': True,}, )
            return user
    except Exception as e:
        services.error_handler(error=e)


if __name__ == '__main__':
    oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)
