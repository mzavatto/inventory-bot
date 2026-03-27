# Admin Catalog Ingestion Module

This module provides a web-based admin interface for uploading and processing catalog PDF files. Authorized administrators can manually upload catalog files to trigger catalog updates.

## Features

### Admin Authentication
- Session-based authentication for admin users
- Secure password verification using constant-time comparison
- Configurable admin credentials via environment variables

### File Upload & Validation
- PDF file upload via drag-and-drop or file picker
- Validation rules:
  - File extension must be `.pdf` (configurable)
  - File must not be empty
  - File size must be under the configured limit (default: 50MB)
  - Duplicate file detection via SHA-256 hash

### PDF Parsing
- **Structured text extraction first** using pdfplumber
- **OCR fallback** for image-based pages (requires pytesseract)
- Extracts:
  - Catalog metadata (name, cycle, edition, date)
  - Sections/categories
  - Products, bundles, combos, replacement parts
  - SKUs and variants
  - Prices (installments, list price, business price, points)
  - Promotions (bank promotions, discounts)

### Import Processing
- Asynchronous background processing
- Real-time status updates via polling
- SKU-based matching for updates
- Fingerprint-based matching for items without SKUs
- Price versioning (historical price preservation)
- Comprehensive import summary

## Admin Web Interface

### Login Page (`/admin/login`)
- Username and password authentication
- Session cookie management
- Redirect to catalog page on successful login

### Catalog Import Page (`/admin/catalog`)
- Drag-and-drop file upload zone
- Upload & Process button
- Real-time processing progress
- Import result summary with statistics:
  - Total items detected
  - New items count
  - Updated items count
  - Price changes count
  - Warnings and errors
- Import history table

### Import Detail Page (`/admin/catalog/imports/{import_id}`)
- Complete file information
- Import statistics
- Warnings and errors list
- Processing log

## API Endpoints

### Authentication
- `POST /admin/login` - Authenticate admin user
- `POST /admin/logout` - Log out admin user
- `GET /admin/me` - Get current admin info

### Catalog Import
- `POST /admin/catalog/upload` - Upload a catalog file
- `POST /admin/catalog/imports/{import_id}/process` - Start processing
- `GET /admin/catalog/imports` - List import history
- `GET /admin/catalog/imports/{import_id}` - Get import details
- `GET /admin/catalog/imports/{import_id}/status` - Get processing status
- `POST /admin/catalog/imports/{import_id}/cancel` - Cancel processing
- `DELETE /admin/catalog/imports/{import_id}` - Delete import record

## Configuration

Add these environment variables to your `.env` file:

```env
# Admin authentication
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password
ADMIN_SECRET_KEY=your-secret-key

# Catalog upload settings
CATALOG_UPLOAD_DIR=uploads
CATALOG_MAX_FILE_SIZE_MB=50
CATALOG_ALLOWED_EXTENSIONS=.pdf
```

## Data Models

### CatalogImport
- `id` - Unique import identifier
- `source_file_name` - Original filename
- `source_file_path` - Storage path
- `source_file_hash` - SHA-256 hash
- `uploaded_by` - Admin username
- `uploaded_at` - Upload timestamp
- `import_status` - pending, processing, completed, failed, cancelled
- `summary` - Import statistics

### CatalogItem
- `item_type` - product, combo, bundle, replacement_part
- `name`, `display_name`, `description`
- `section_name`, `line`, `material`, `color`
- `dimensions`, `size_cm`, `capacity_liters`, `shape`
- `page_number`, `raw_extracted_text`
- `skus` - List of SKU information
- `prices` - List of price information
- `fingerprint` - For matching items without SKUs

### CatalogPrice
- Multiple price types: installments, list price, business price
- Points: puntos, puntos_essen_plus, puntos_xl
- Validity dates for versioning

## Usage

1. Navigate to `/admin/login`
2. Enter your admin credentials
3. On the Catalog Import page:
   - Drag and drop a PDF file, or click to browse
   - Click "Upload & Process"
   - Wait for processing to complete
   - Review the import summary
4. View import history to inspect past imports

## Future Extensibility

The module is designed for future enhancements:
- Additional file formats (Excel, CSV)
- WhatsApp-based file upload (parsing core is reusable)
- Dry-run mode for previewing changes
- Malware scanning hook
- Database persistence (currently in-memory)

## Testing

Run the tests with:

```bash
python -m pytest tests/test_admin_catalog.py -v
```

Test coverage includes:
- Admin authentication
- File validation
- Parser helper functions
- Import service operations
- SKU matching logic
- Bundle/price extraction
