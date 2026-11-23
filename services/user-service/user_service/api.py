from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, get_current_user, require_admin, require_internal

from .schemas import ProfileCreate, ProfileOut, ProfilePage, ProfileUpdate
from .service import UserService

router = APIRouter(prefix="/users", tags=["users"])
internal_router = APIRouter(prefix="/internal/users", tags=["internal"], dependencies=[Depends(require_internal)])


def get_service(session: AsyncSession = Depends(get_session)) -> UserService:
    return UserService(session)


@router.get("/me", response_model=ProfileOut, summary="Current user's profile", responses=error_responses(401, 404))
async def get_me(user: CurrentUser = Depends(get_current_user), service: UserService = Depends(get_service)):
    return await service.get(user.id, user)


@router.patch(
    "/me", response_model=ProfileOut, summary="Update own profile", responses=error_responses(400, 401, 404, 422)
)
async def update_me(
    data: ProfileUpdate, user: CurrentUser = Depends(get_current_user), service: UserService = Depends(get_service)
):
    return await service.update(user.id, data)


@router.get(
    "/{user_id}", response_model=ProfileOut, summary="Profile by id (self or admin)", responses=error_responses(401, 403, 404)
)
async def get_user(user_id: int, user: CurrentUser = Depends(get_current_user), service: UserService = Depends(get_service)):
    return await service.get(user_id, user)


@router.get("", response_model=ProfilePage, summary="List profiles (admin)", responses=error_responses(401, 403))
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: CurrentUser = Depends(require_admin),
    service: UserService = Depends(get_service),
):
    items, total = await service.list_all(limit, offset)
    return ProfilePage(items=items, total=total, limit=limit, offset=offset)


@internal_router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED, summary="Create profile (auth-service)")
async def create_profile(data: ProfileCreate, response: Response, service: UserService = Depends(get_service)):
    profile, created = await service.create(data)
    if not created:
        response.status_code = status.HTTP_200_OK
    return profile
