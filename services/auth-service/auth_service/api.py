from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, get_current_user

from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from .service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> AuthService:
    state = request.app.state
    return AuthService(session, state.settings, state.user_client, state.bus)


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new customer",
    responses=error_responses(409, 422, 502, 504),
)
async def register(data: RegisterRequest, service: AuthService = Depends(get_service)) -> UserOut:
    credential = await service.register(data)
    return UserOut.model_validate(credential)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a JWT access token",
    responses=error_responses(401, 403, 422),
)
async def login(data: LoginRequest, request: Request, service: AuthService = Depends(get_service)) -> TokenResponse:
    token, credential = await service.login(data.email, data.password)
    return TokenResponse(
        access_token=token,
        expires_in=request.app.state.settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(credential),
    )


@router.get("/me", response_model=UserOut, summary="Identity encoded in the token", responses=error_responses(401))
async def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role)
