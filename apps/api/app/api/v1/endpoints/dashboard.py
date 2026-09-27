"""Admin dashboard endpoint.

One route returning the whole page: seven cards, four charts, three tables. The
alternative — an endpoint per widget — makes the page render eleven times and
turns the dashboard into eleven things to keep in sync.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, RequirePermission
from app.core.permissions import Permission
from app.models.user import User
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard_service import DashboardService
from fastapi import Depends

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

CanView = Annotated[User, Depends(RequirePermission(Permission.BUILDING_READ))]


@router.get(
    "",
    response_model=DashboardResponse,
    summary="The whole admin dashboard in one response",
    description=(
        "**Cards** — total buildings, total units, total ULPINs, verified, "
        "pending, fraud alerts, active tenants.\n\n"
        "**Charts** — property distribution by type, verification status, fraud "
        "trend by severity, occupancy movement.\n\n"
        "**Tables** — recent activities from the audit log, recent verifications, "
        "open fraud alerts.\n\n"
        "The verified/pending split counts *properties*, not verification runs: a "
        "unit verified four times counts once. Charts are pre-shaped for Recharts "
        "— one flat object per x-value — so the chart components hold no "
        "reshaping logic."
    ),
)
async def dashboard(
    session: DbSession,
    actor: CanView,
    trend_days: Annotated[int, Query(ge=7, le=365, description="Window for the two trend charts")] = 30,
) -> DashboardResponse:
    return await DashboardService(session).overview(actor=actor, trend_days=trend_days)
