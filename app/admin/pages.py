"""Admin web page routes."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin.auth import get_optional_admin, get_session, AdminUser
from app.services.ingestion.import_service import get_import_service, ImportNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-pages"])


def _render_login_page(error: str | None = None) -> str:
    """Render the login page HTML."""
    error_html = ""
    if error:
        error_html = f'<div class="error-message">{error}</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login - Inventory Bot</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .login-container {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{
            text-align: center;
            color: #333;
            margin-bottom: 8px;
            font-size: 24px;
        }}
        .subtitle {{
            text-align: center;
            color: #666;
            margin-bottom: 32px;
            font-size: 14px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
            font-size: 14px;
        }}
        input[type="text"],
        input[type="password"] {{
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.2s, box-shadow 0.2s;
        }}
        input:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2);
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }}
        button:active {{
            transform: translateY(0);
        }}
        .error-message {{
            background: #fee2e2;
            color: #dc2626;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
            border: 1px solid #fecaca;
        }}
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🔐 Admin Login</h1>
        <p class="subtitle">Inventory Bot - Catalog Management</p>
        {error_html}
        <form method="POST" action="/admin/login">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <input type="hidden" name="redirect_to" value="/admin/catalog">
            <button type="submit">Log In</button>
        </form>
    </div>
</body>
</html>"""


def _render_catalog_page(username: str, imports_html: str) -> str:
    """Render the catalog import page HTML."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Catalog Import - Admin</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .header h1 {{
            font-size: 24px;
            font-weight: 600;
        }}
        .user-info {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .user-info span {{
            opacity: 0.9;
        }}
        .logout-btn {{
            background: rgba(255,255,255,0.2);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.2s;
        }}
        .logout-btn:hover {{
            background: rgba(255,255,255,0.3);
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        .upload-section {{
            background: white;
            border-radius: 16px;
            padding: 32px;
            margin-bottom: 32px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .section-title {{
            font-size: 20px;
            font-weight: 600;
            color: #333;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .drop-zone {{
            border: 2px dashed #d0d0d0;
            border-radius: 12px;
            padding: 60px 40px;
            text-align: center;
            transition: all 0.3s;
            cursor: pointer;
            background: #fafafa;
        }}
        .drop-zone:hover,
        .drop-zone.dragover {{
            border-color: #667eea;
            background: #f0f4ff;
        }}
        .drop-zone.has-file {{
            border-color: #22c55e;
            background: #f0fdf4;
        }}
        .drop-zone-icon {{
            font-size: 48px;
            margin-bottom: 16px;
        }}
        .drop-zone-text {{
            color: #666;
            font-size: 16px;
            margin-bottom: 8px;
        }}
        .drop-zone-subtext {{
            color: #999;
            font-size: 14px;
        }}
        .file-input {{
            display: none;
        }}
        .file-info {{
            display: none;
            margin-top: 20px;
            padding: 16px;
            background: #f0fdf4;
            border-radius: 8px;
            border: 1px solid #bbf7d0;
        }}
        .file-info.visible {{
            display: block;
        }}
        .file-name {{
            font-weight: 600;
            color: #166534;
            margin-bottom: 4px;
        }}
        .file-size {{
            color: #15803d;
            font-size: 14px;
        }}
        .button-group {{
            display: flex;
            gap: 12px;
            margin-top: 24px;
            justify-content: center;
        }}
        .btn {{
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            border: none;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .btn-primary:hover:not(:disabled) {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }}
        .btn-primary:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        .btn-secondary {{
            background: #e5e7eb;
            color: #374151;
        }}
        .btn-secondary:hover {{
            background: #d1d5db;
        }}
        .progress-section {{
            display: none;
            margin-top: 24px;
            padding: 20px;
            background: #f8fafc;
            border-radius: 8px;
        }}
        .progress-section.visible {{
            display: block;
        }}
        .progress-bar {{
            height: 8px;
            background: #e5e7eb;
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 12px;
        }}
        .progress-fill {{
            height: 100%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 4px;
            transition: width 0.3s;
            width: 0%;
        }}
        .progress-text {{
            text-align: center;
            color: #666;
            font-size: 14px;
        }}
        .result-section {{
            display: none;
            margin-top: 24px;
            padding: 20px;
            border-radius: 8px;
        }}
        .result-section.visible {{
            display: block;
        }}
        .result-section.success {{
            background: #f0fdf4;
            border: 1px solid #bbf7d0;
        }}
        .result-section.error {{
            background: #fef2f2;
            border: 1px solid #fecaca;
        }}
        .result-title {{
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .result-section.success .result-title {{
            color: #166534;
        }}
        .result-section.error .result-title {{
            color: #dc2626;
        }}
        .result-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 16px;
            margin-top: 16px;
        }}
        .stat-item {{
            text-align: center;
            padding: 12px;
            background: white;
            border-radius: 8px;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: 700;
            color: #333;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }}
        .history-section {{
            background: white;
            border-radius: 16px;
            padding: 32px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .history-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }}
        .history-table th,
        .history-table td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid #e5e7eb;
        }}
        .history-table th {{
            background: #f8fafc;
            font-weight: 600;
            color: #374151;
            font-size: 14px;
        }}
        .history-table tr:hover {{
            background: #f8fafc;
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-pending {{
            background: #fef3c7;
            color: #92400e;
        }}
        .status-processing {{
            background: #dbeafe;
            color: #1e40af;
        }}
        .status-completed {{
            background: #dcfce7;
            color: #166534;
        }}
        .status-failed {{
            background: #fee2e2;
            color: #dc2626;
        }}
        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
        .empty-state-icon {{
            font-size: 48px;
            margin-bottom: 16px;
        }}
        .action-btn {{
            padding: 6px 12px;
            font-size: 12px;
            border-radius: 6px;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }}
        .action-btn-view {{
            background: #e0e7ff;
            color: #3730a3;
        }}
        .action-btn-view:hover {{
            background: #c7d2fe;
        }}
    </style>
</head>
<body>
    <header class="header">
        <h1>📦 Catalog Import</h1>
        <div class="user-info">
            <span>👤 {username}</span>
            <form method="POST" action="/admin/logout" style="display: inline;">
                <button type="submit" class="logout-btn">Log Out</button>
            </form>
        </div>
    </header>

    <div class="container">
        <section class="upload-section">
            <h2 class="section-title">📤 Upload New Catalog</h2>

            <div class="drop-zone" id="dropZone">
                <div class="drop-zone-icon">📄</div>
                <div class="drop-zone-text">Drag and drop your catalog PDF here</div>
                <div class="drop-zone-subtext">or click to browse files</div>
            </div>
            <input type="file" class="file-input" id="fileInput" accept=".pdf">

            <div class="file-info" id="fileInfo">
                <div class="file-name" id="fileName"></div>
                <div class="file-size" id="fileSize"></div>
            </div>

            <div class="button-group">
                <button class="btn btn-primary" id="uploadBtn" disabled>Upload & Process</button>
                <button class="btn btn-secondary" id="clearBtn" style="display: none;">Clear</button>
            </div>

            <div class="progress-section" id="progressSection">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="progress-text" id="progressText">Uploading...</div>
            </div>

            <div class="result-section" id="resultSection">
                <div class="result-title" id="resultTitle"></div>
                <div class="result-stats" id="resultStats"></div>
            </div>
        </section>

        <section class="history-section">
            <h2 class="section-title">📋 Import History</h2>
            {imports_html}
        </section>
    </div>

    <script>
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const fileInfo = document.getElementById('fileInfo');
        const fileName = document.getElementById('fileName');
        const fileSize = document.getElementById('fileSize');
        const uploadBtn = document.getElementById('uploadBtn');
        const clearBtn = document.getElementById('clearBtn');
        const progressSection = document.getElementById('progressSection');
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        const resultSection = document.getElementById('resultSection');
        const resultTitle = document.getElementById('resultTitle');
        const resultStats = document.getElementById('resultStats');

        let selectedFile = null;

        // Drag and drop handlers
        dropZone.addEventListener('click', () => fileInput.click());

        dropZone.addEventListener('dragover', (e) => {{
            e.preventDefault();
            dropZone.classList.add('dragover');
        }});

        dropZone.addEventListener('dragleave', () => {{
            dropZone.classList.remove('dragover');
        }});

        dropZone.addEventListener('drop', (e) => {{
            e.preventDefault();
            dropZone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {{
                handleFile(files[0]);
            }}
        }});

        fileInput.addEventListener('change', (e) => {{
            if (e.target.files.length > 0) {{
                handleFile(e.target.files[0]);
            }}
        }});

        function handleFile(file) {{
            // Client-side validation (server also validates)
            if (!file.name.toLowerCase().endsWith('.pdf')) {{
                showError('Only PDF files are allowed. Please select a .pdf file.');
                return;
            }}
            
            // Check file size (50MB default limit)
            const maxSize = 50 * 1024 * 1024;
            if (file.size > maxSize) {{
                showError('File is too large. Maximum file size is 50MB.');
                return;
            }}

            selectedFile = file;
            fileName.textContent = file.name;
            fileSize.textContent = formatFileSize(file.size);
            fileInfo.classList.add('visible');
            dropZone.classList.add('has-file');
            uploadBtn.disabled = false;
            clearBtn.style.display = 'inline-block';
            resultSection.classList.remove('visible');
        }}

        function formatFileSize(bytes) {{
            if (bytes < 1024) return bytes + ' bytes';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }}

        clearBtn.addEventListener('click', () => {{
            selectedFile = null;
            fileInput.value = '';
            fileInfo.classList.remove('visible');
            dropZone.classList.remove('has-file');
            uploadBtn.disabled = true;
            clearBtn.style.display = 'none';
            resultSection.classList.remove('visible');
        }});

        uploadBtn.addEventListener('click', async () => {{
            if (!selectedFile) return;

            uploadBtn.disabled = true;
            progressSection.classList.add('visible');
            progressFill.style.width = '10%';
            progressText.textContent = 'Uploading file...';
            resultSection.classList.remove('visible');

            try {{
                // Upload file
                const formData = new FormData();
                formData.append('file', selectedFile);

                progressFill.style.width = '30%';
                progressText.textContent = 'Uploading...';

                const uploadResponse = await fetch('/admin/catalog/upload', {{
                    method: 'POST',
                    body: formData,
                }});

                if (!uploadResponse.ok) {{
                    const error = await uploadResponse.json();
                    throw new Error(error.detail || 'Upload failed');
                }}

                const uploadResult = await uploadResponse.json();
                const importId = uploadResult.id;

                progressFill.style.width = '50%';
                progressText.textContent = 'Processing catalog...';

                // Start processing
                const processResponse = await fetch(`/admin/catalog/imports/${{importId}}/process`, {{
                    method: 'POST',
                }});

                if (!processResponse.ok) {{
                    const error = await processResponse.json();
                    throw new Error(error.detail || 'Processing failed');
                }}

                // Poll for status
                let status = 'processing';
                while (status === 'processing') {{
                    await new Promise(r => setTimeout(r, 1000));

                    const statusResponse = await fetch(`/admin/catalog/imports/${{importId}}/status`);
                    const statusResult = await statusResponse.json();
                    status = statusResult.status;

                    if (status === 'processing') {{
                        const progress = Math.min(90, parseInt(progressFill.style.width) + 5);
                        progressFill.style.width = progress + '%';
                    }}
                }}

                progressFill.style.width = '100%';
                progressText.textContent = 'Complete!';

                // Show result
                const finalResponse = await fetch(`/admin/catalog/imports/${{importId}}`);
                const finalResult = await finalResponse.json();

                setTimeout(() => {{
                    progressSection.classList.remove('visible');
                    showResult(finalResult);
                    location.reload(); // Refresh to show in history
                }}, 500);

            }} catch (error) {{
                progressSection.classList.remove('visible');
                showError(error.message);
                uploadBtn.disabled = false;
            }}
        }});

        function showResult(result) {{
            resultSection.classList.add('visible');
            if (result.import_status === 'completed') {{
                resultSection.classList.remove('error');
                resultSection.classList.add('success');
                resultTitle.innerHTML = '✅ Import Completed Successfully';
                resultStats.innerHTML = `
                    <div class="stat-item">
                        <div class="stat-value">${{result.summary.total_items_detected}}</div>
                        <div class="stat-label">Items Detected</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${{result.summary.new_items_count}}</div>
                        <div class="stat-label">New Items</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${{result.summary.updated_items_count}}</div>
                        <div class="stat-label">Updated</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">${{result.summary.warnings_count}}</div>
                        <div class="stat-label">Warnings</div>
                    </div>
                `;
            }} else {{
                showError('Import failed: ' + (result.summary.errors[0] || 'Unknown error'));
            }}
        }}

        function showError(message) {{
            resultSection.classList.add('visible');
            resultSection.classList.remove('success');
            resultSection.classList.add('error');
            resultTitle.innerHTML = '❌ Import Failed';
            resultStats.innerHTML = `<p>${{message}}</p>`;
        }}
    </script>
</body>
</html>"""


def _render_imports_table(imports: list) -> str:
    """Render the imports history table HTML."""
    if not imports:
        return """
        <div class="empty-state">
            <div class="empty-state-icon">📭</div>
            <p>No import history yet</p>
        </div>
        """

    rows = []
    for imp in imports:
        status_class = f"status-{imp.import_status.lower()}"
        uploaded_at = imp.uploaded_at.strftime("%Y-%m-%d %H:%M") if imp.uploaded_at else "-"
        rows.append(f"""
            <tr>
                <td>{imp.source_file_name}</td>
                <td>{imp.uploaded_by}</td>
                <td>{uploaded_at}</td>
                <td><span class="status-badge {status_class}">{imp.import_status.upper()}</span></td>
                <td>{imp.total_items_detected}</td>
                <td>{imp.new_items_count}</td>
                <td>{imp.errors_count}</td>
                <td>
                    <a href="/admin/catalog/imports/{imp.id}" class="action-btn action-btn-view">View</a>
                </td>
            </tr>
        """)

    return f"""
        <table class="history-table">
            <thead>
                <tr>
                    <th>File</th>
                    <th>Uploaded By</th>
                    <th>Date</th>
                    <th>Status</th>
                    <th>Items</th>
                    <th>New</th>
                    <th>Errors</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    """


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    admin_token: Annotated[str | None, Cookie()] = None,
) -> HTMLResponse:
    """Render the login page."""
    # If already logged in, redirect to catalog
    if admin_token and get_session(admin_token):
        return RedirectResponse(url="/admin/catalog", status_code=303)

    return HTMLResponse(content=_render_login_page())


@router.get("/catalog", response_class=HTMLResponse)
async def catalog_page(
    admin_token: Annotated[str | None, Cookie()] = None,
) -> HTMLResponse:
    """Render the catalog import page."""
    # Check authentication
    if not admin_token:
        return RedirectResponse(url="/admin/login", status_code=303)

    session = get_session(admin_token)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=303)

    # Get import history
    service = get_import_service()
    imports = service.list_imports(limit=20)
    imports_html = _render_imports_table(imports)

    return HTMLResponse(content=_render_catalog_page(session.user.username, imports_html))


@router.get("/catalog/imports/{import_id}", response_class=HTMLResponse)
async def import_detail_page(
    import_id: str,
    admin_token: Annotated[str | None, Cookie()] = None,
) -> HTMLResponse:
    """Render the import detail page."""
    # Check authentication
    if not admin_token:
        return RedirectResponse(url="/admin/login", status_code=303)

    session = get_session(admin_token)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=303)

    # Get import details
    service = get_import_service()
    try:
        catalog_import = service.get_import(import_id)
    except ImportNotFoundError:
        return HTMLResponse(content="Import not found", status_code=404)

    # Render detail page
    return HTMLResponse(content=_render_import_detail_page(session.user.username, catalog_import))


def _render_import_detail_page(username: str, catalog_import) -> str:
    """Render the import detail page HTML."""
    status_class = f"status-{catalog_import.import_status.lower()}"
    uploaded_at = catalog_import.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if catalog_import.uploaded_at else "-"
    started_at = catalog_import.started_at.strftime("%Y-%m-%d %H:%M:%S") if catalog_import.started_at else "-"
    finished_at = catalog_import.finished_at.strftime("%Y-%m-%d %H:%M:%S") if catalog_import.finished_at else "-"

    # Build log HTML
    log_html = "\n".join(f"<div class='log-entry'>{log}</div>" for log in catalog_import.raw_log)

    # Build warnings HTML
    warnings_html = ""
    if catalog_import.summary.warnings:
        warnings_items = "".join(f"<li>{w}</li>" for w in catalog_import.summary.warnings)
        warnings_html = f"<ul class='warning-list'>{warnings_items}</ul>"

    # Build errors HTML
    errors_html = ""
    if catalog_import.summary.errors:
        errors_items = "".join(f"<li>{e}</li>" for e in catalog_import.summary.errors)
        errors_html = f"<ul class='error-list'>{errors_items}</ul>"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Import Details - Admin</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header h1 {{
            font-size: 24px;
        }}
        .back-link {{
            color: white;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .back-link:hover {{
            opacity: 0.9;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 32px;
            margin-bottom: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .card-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 20px;
            color: #333;
        }}
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }}
        .info-item {{
            padding: 16px;
            background: #f8fafc;
            border-radius: 8px;
        }}
        .info-label {{
            font-size: 12px;
            color: #666;
            margin-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .info-value {{
            font-size: 16px;
            font-weight: 600;
            color: #333;
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-pending {{ background: #fef3c7; color: #92400e; }}
        .status-processing {{ background: #dbeafe; color: #1e40af; }}
        .status-completed {{ background: #dcfce7; color: #166534; }}
        .status-failed {{ background: #fee2e2; color: #dc2626; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
            gap: 16px;
            margin-top: 16px;
        }}
        .stat-box {{
            text-align: center;
            padding: 16px;
            background: #f8fafc;
            border-radius: 8px;
        }}
        .stat-value {{
            font-size: 28px;
            font-weight: 700;
            color: #333;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }}
        .log-container {{
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
            border-radius: 8px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 13px;
            max-height: 400px;
            overflow-y: auto;
        }}
        .log-entry {{
            margin-bottom: 4px;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        .warning-list {{
            background: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 8px;
            padding: 16px 16px 16px 32px;
            color: #92400e;
        }}
        .error-list {{
            background: #fee2e2;
            border: 1px solid #fca5a5;
            border-radius: 8px;
            padding: 16px 16px 16px 32px;
            color: #dc2626;
        }}
        .warning-list li, .error-list li {{
            margin-bottom: 8px;
        }}
    </style>
</head>
<body>
    <header class="header">
        <a href="/admin/catalog" class="back-link">← Back to Catalog</a>
        <h1>Import Details</h1>
        <span>👤 {username}</span>
    </header>

    <div class="container">
        <div class="card">
            <h2 class="card-title">📄 File Information</h2>
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">File Name</div>
                    <div class="info-value">{catalog_import.source_file_name}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">File Size</div>
                    <div class="info-value">{catalog_import.file_size_bytes / 1024 / 1024:.2f} MB</div>
                </div>
                <div class="info-item">
                    <div class="info-label">File Hash</div>
                    <div class="info-value" style="font-size: 12px;">{catalog_import.source_file_hash[:16]}...</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Status</div>
                    <div class="info-value"><span class="status-badge {status_class}">{catalog_import.import_status.upper()}</span></div>
                </div>
                <div class="info-item">
                    <div class="info-label">Uploaded By</div>
                    <div class="info-value">{catalog_import.uploaded_by}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Uploaded At</div>
                    <div class="info-value">{uploaded_at}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Started At</div>
                    <div class="info-value">{started_at}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Finished At</div>
                    <div class="info-value">{finished_at}</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2 class="card-title">📊 Import Summary</h2>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.total_items_detected}</div>
                    <div class="stat-label">Total Items</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.new_items_count}</div>
                    <div class="stat-label">New</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.updated_items_count}</div>
                    <div class="stat-label">Updated</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.changed_prices_count}</div>
                    <div class="stat-label">Price Changes</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.sections_detected}</div>
                    <div class="stat-label">Sections</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.warnings_count}</div>
                    <div class="stat-label">Warnings</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{catalog_import.summary.errors_count}</div>
                    <div class="stat-label">Errors</div>
                </div>
            </div>
        </div>

        {f'<div class="card"><h2 class="card-title">⚠️ Warnings</h2>{warnings_html}</div>' if warnings_html else ''}

        {f'<div class="card"><h2 class="card-title">❌ Errors</h2>{errors_html}</div>' if errors_html else ''}

        <div class="card">
            <h2 class="card-title">📜 Processing Log</h2>
            <div class="log-container">
                {log_html if log_html else '<div class="log-entry">No log entries</div>'}
            </div>
        </div>
    </div>
</body>
</html>"""
