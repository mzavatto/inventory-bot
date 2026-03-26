"""Admin API endpoints for catalog management."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin.auth import (
    AdminUser,
    CurrentAdmin,
    create_session,
    invalidate_session,
    verify_credentials,
)
from app.admin.models import (
    CatalogImport,
    CatalogImportRequest,
    CatalogImportResponse,
    ImportHistoryItem,
    ImportStatus,
)
from app.services.ingestion.import_service import (
    FileValidationError,
    ImportNotFoundError,
    get_import_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Authentication endpoints ---


@router.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    redirect_to: str = Form(default="/admin/catalog"),
) -> RedirectResponse:
    """Authenticate admin user and set session cookie."""
    if not verify_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session = create_session(username)

    # Set cookie and redirect
    redirect = RedirectResponse(url=redirect_to, status_code=303)
    redirect.set_cookie(
        key="admin_token",
        value=session.token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=86400,  # 24 hours
    )
    return redirect


@router.post("/logout")
async def logout(
    response: Response,
    admin_token: str | None = None,
) -> RedirectResponse:
    """Log out admin user."""
    if admin_token:
        invalidate_session(admin_token)

    redirect = RedirectResponse(url="/admin/login", status_code=303)
    redirect.delete_cookie("admin_token")
    return redirect


@router.get("/me")
async def get_current_admin_info(current_admin: CurrentAdmin) -> dict:
    """Get information about the current admin user."""
    return {
        "username": current_admin.username,
        "authenticated_at": current_admin.authenticated_at.isoformat(),
    }


# --- Catalog import API endpoints ---


@router.post("/catalog/upload", response_model=CatalogImport)
async def upload_catalog(
    current_admin: CurrentAdmin,
    file: UploadFile = File(...),
) -> CatalogImport:
    """Upload a catalog file for import.

    Validates the file and stores it for processing.
    Returns the import record with pending status.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    service = get_import_service()

    try:
        # Read file into memory for validation and hashing
        content = await file.read()
        await file.seek(0)

        # Create a file-like object from bytes
        import io

        file_obj = io.BytesIO(content)

        catalog_import = await service.upload_file(
            file=file_obj,
            filename=file.filename,
            file_size=len(content),
            content_type=file.content_type,
            uploaded_by=current_admin.username,
        )

        return catalog_import

    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error uploading catalog: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.post("/catalog/imports/{import_id}/process", response_model=CatalogImportResponse)
async def start_import_processing(
    import_id: str,
    current_admin: CurrentAdmin,
    dry_run: bool = Query(default=False),
) -> CatalogImportResponse:
    """Start processing an uploaded catalog file.

    Args:
        import_id: ID of the import to process.
        dry_run: If True, parse but don't apply changes.

    Returns:
        Import response with current status.
    """
    service = get_import_service()

    try:
        catalog_import = await service.start_processing(import_id, dry_run=dry_run)

        return CatalogImportResponse(
            import_id=catalog_import.id,
            status=catalog_import.import_status,
            message="Processing started" if catalog_import.import_status == ImportStatus.PROCESSING else "Already processed",
            summary=catalog_import.summary,
        )

    except ImportNotFoundError:
        raise HTTPException(status_code=404, detail="Import not found")
    except Exception as exc:
        logger.exception("Error starting import processing: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to start processing")


@router.get("/catalog/imports", response_model=list[ImportHistoryItem])
async def list_imports(
    current_admin: CurrentAdmin,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ImportHistoryItem]:
    """List catalog import history."""
    service = get_import_service()
    return service.list_imports(limit=limit, offset=offset)


@router.get("/catalog/imports/{import_id}", response_model=CatalogImport)
async def get_import(
    import_id: str,
    current_admin: CurrentAdmin,
) -> CatalogImport:
    """Get details of a specific import."""
    service = get_import_service()

    try:
        return service.get_import(import_id)
    except ImportNotFoundError:
        raise HTTPException(status_code=404, detail="Import not found")


@router.get("/catalog/imports/{import_id}/status", response_model=CatalogImportResponse)
async def get_import_status(
    import_id: str,
    current_admin: CurrentAdmin,
) -> CatalogImportResponse:
    """Get the current status of an import (for polling)."""
    service = get_import_service()

    try:
        catalog_import = service.get_import(import_id)

        return CatalogImportResponse(
            import_id=catalog_import.id,
            status=catalog_import.import_status,
            message=_status_message(catalog_import.import_status),
            summary=catalog_import.summary if catalog_import.import_status != ImportStatus.PENDING else None,
        )

    except ImportNotFoundError:
        raise HTTPException(status_code=404, detail="Import not found")


@router.post("/catalog/imports/{import_id}/cancel")
async def cancel_import(
    import_id: str,
    current_admin: CurrentAdmin,
) -> dict:
    """Cancel a running import process."""
    service = get_import_service()

    cancelled = await service.cancel_processing(import_id)
    if cancelled:
        return {"message": "Import cancelled"}
    else:
        return {"message": "Import was not running"}


@router.delete("/catalog/imports/{import_id}")
async def delete_import(
    import_id: str,
    current_admin: CurrentAdmin,
) -> dict:
    """Delete an import record and its associated file."""
    service = get_import_service()

    deleted = service.delete_import(import_id)
    if deleted:
        return {"message": "Import deleted"}
    else:
        raise HTTPException(status_code=404, detail="Import not found or cannot be deleted")


def _status_message(status: ImportStatus) -> str:
    """Get a human-readable message for an import status."""
    messages = {
        ImportStatus.PENDING: "Waiting to process",
        ImportStatus.VALIDATING: "Validating file",
        ImportStatus.PROCESSING: "Processing catalog data",
        ImportStatus.COMPLETED: "Import completed successfully",
        ImportStatus.FAILED: "Import failed",
        ImportStatus.CANCELLED: "Import was cancelled",
    }
    return messages.get(status, "Unknown status")
