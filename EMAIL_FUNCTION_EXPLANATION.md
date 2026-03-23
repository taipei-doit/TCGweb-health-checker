# Email Function Explanation

## Overview

The email function automatically packages and sends crawl results via Gmail SMTP after the crawling task completes. It's implemented in `utils/email_reporter.py` and used by all main scripts.

## How It Works

### 1. **Initialization** (`EmailReporter.__init__`)

When `EmailReporter` is created, it:
- Loads credentials from `.env` file:
  - `GMAIL_USER`: Your Gmail address
  - `GMAIL_APP_PASSWORD`: Gmail App Password (not regular password!)
  - `TO_EMAIL`: Recipient email (defaults to `GMAIL_USER` if not set)
- Sets maximum ZIP size to 20MB (Gmail limit is 25MB)
- Validates that credentials are present

```python
email_reporter = EmailReporter()
# Loads from .env:
# GMAIL_USER=your-email@gmail.com
# GMAIL_APP_PASSWORD=your-app-password
# TO_EMAIL=recipient@gmail.com (optional)
```

### 2. **Two Sending Methods**

#### **Method 1: `pack_and_send_simple()`**
- **Use case**: Small datasets (< 20MB total)
- **Behavior**: Creates a single ZIP file with all results
- **Contents**:
  - Excel report (`website_summary_report_YYYY-MM.xlsx`)
  - Execution log (`crawler_execution.log`)
  - All files from `assets/` folder
- **Limitation**: Will fail if total size exceeds 25MB

#### **Method 2: `pack_and_send_seperate()`** ⭐ **Currently Used**
- **Use case**: Large datasets (recommended)
- **Behavior**: Intelligently splits data into multiple ZIP files
- **Strategy**:
  1. **Part 1**: Excel report + execution log (always included)
  2. **Part 2+**: Website data folders from `assets/`, split to stay under 20MB each
- **Advantage**: Handles any size of data by splitting into multiple emails

### 3. **Packaging Process** (`pack_and_send_seperate`)

```
Step 1: Create Part 1 (Essential Files)
├── Excel report (website_summary_report_YYYY-MM.xlsx)
└── Execution log (crawler_execution.log)

Step 2: Process Assets Folder
├── For each website folder in assets/:
│   ├── Add all files from folder to current ZIP
│   ├── Check if ZIP size > 20MB
│   └── If yes, close current ZIP and start new one
└── Continue until all websites are packaged

Step 3: Send All Parts
├── Send Part 1/Total
├── Send Part 2/Total
├── ...
└── Clean up ZIP files after sending
```

### 4. **Email Sending** (`_send_part`)

For each ZIP file:
1. **Create email message**:
   - Subject: `網站爬蟲數據包 Part X/Y - YYYY-MM-DD`
   - From: `GMAIL_USER`
   - To: `TO_EMAIL`
   - Body: Lists files in ZIP, size info, part number

2. **Attach ZIP file**:
   - Reads ZIP file as binary
   - Attaches as MIME application

3. **Send via Gmail SMTP**:
   - Connects to `smtp.gmail.com:465` (SSL)
   - Authenticates with `GMAIL_USER` and `GMAIL_APP_PASSWORD`
   - Sends email

4. **Cleanup**:
   - Deletes ZIP file after successful send

## Configuration

### Required Environment Variables

Create a `.env` file in the project root:

```bash
# Gmail credentials
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
TO_EMAIL=recipient@gmail.com  # Optional, defaults to GMAIL_USER
```

### Getting Gmail App Password

1. Go to your Google Account settings
2. Enable 2-Step Verification (required)
3. Go to "App passwords" section
4. Generate a new app password for "Mail"
5. Copy the 16-character password (spaces optional)
6. Use it as `GMAIL_APP_PASSWORD` in `.env`

**Important**: Use App Password, NOT your regular Gmail password!

## Usage in Code

The email function is called automatically after crawling completes:

```python
# In gcp_main_mpfast.py, gcp_main_mpselfqueue.py, main.py

def pack_and_send_email(excel_report_path):
    """Called after all sites are processed"""
    email_reporter = EmailReporter()
    success = email_reporter.pack_and_send_seperate(excel_report_path)
    # Returns True if all parts sent successfully
```

### When It's Called

1. **After successful completion**: All sites processed → Send email → Shutdown VM
2. **After partial completion**: Some sites failed → Still sends email with available data
3. **On error**: Email sending fails gracefully, doesn't crash the program

## Email Content Example

**Subject**: `網站爬蟲數據包 Part 1/3 - 2025-01-15`

**Body**:
```
爬蟲任務已完成。

這是 3 封郵件中的第 1 封。

此壓縮檔包含以下內容：
- website_summary_report_2025-01.xlsx
- crawler_execution.log

壓縮檔大小: 2.45 MB

(這是由 GCP VM 自動發送)
```

**Attachment**: `crawl_data_part1.zip`

## File Structure in ZIP

### Part 1 (Essential Files)
```
crawl_data_part1.zip
├── website_summary_report_2025-01.xlsx
└── crawler_execution.log
```

### Part 2+ (Website Data)
```
crawl_data_part2.zip
├── assets/
│   ├── 網站A/
│   │   ├── page_summary.json
│   │   ├── crawl_log.txt
│   │   └── error_pages.csv
│   └── 網站B/
│       └── ...
```

## Error Handling

- **Missing credentials**: Prints warning, skips email, continues execution
- **SMTP connection failure**: Prints error, continues execution
- **File not found**: Warns but continues with available files
- **ZIP too large**: Automatically splits (in `pack_and_send_seperate`)
- **Send failure**: Logs error, cleans up files, returns `False`

## Size Limits

- **Gmail attachment limit**: 25MB per email
- **Code limit**: 20MB per ZIP (safety margin)
- **Automatic splitting**: If data exceeds 20MB, creates multiple parts

## Troubleshooting

### Email Not Sending

1. **Check `.env` file exists**:
   ```bash
   ls -la .env
   ```

2. **Verify credentials**:
   ```bash
   cat .env | grep GMAIL
   ```

3. **Test App Password**:
   - Make sure 2-Step Verification is enabled
   - Regenerate App Password if needed

4. **Check logs**:
   ```bash
   tail -f ~/crawler_execution.log
   ```

### "EmailReporter 憑證無效"

- `.env` file missing or incomplete
- `GMAIL_USER` or `GMAIL_APP_PASSWORD` not set
- Check file permissions: `chmod 600 .env`

### "Email 發送失敗"

- Network connectivity issue
- Gmail SMTP blocked (check firewall)
- App Password expired (regenerate)
- Check Gmail account security settings

## Code Flow Diagram

```
Crawling Complete
    ↓
pack_and_send_email(excel_path)
    ↓
EmailReporter.__init__()
    ↓ (loads .env)
pack_and_send_seperate()
    ↓
Create Part 1 (Excel + Log)
    ↓
Process assets/ folder
    ↓
Split into Parts 2, 3, ... (if needed)
    ↓
For each part:
    ↓
_send_part()
    ↓
SMTP Send → Gmail
    ↓
Delete ZIP file
    ↓
Return success/failure
```

## Security Notes

- **Never commit `.env` file** to Git (should be in `.gitignore`)
- **Use App Passwords**, not regular passwords
- **Restrict `.env` permissions**: `chmod 600 .env`
- **Rotate App Passwords** periodically

## Summary

The email function:
1. ✅ Automatically packages crawl results into ZIP files
2. ✅ Intelligently splits large datasets into multiple emails
3. ✅ Sends via Gmail SMTP with proper authentication
4. ✅ Handles errors gracefully without crashing
5. ✅ Cleans up temporary files after sending
6. ✅ Provides detailed progress logging

It's designed to work seamlessly in automated GCP VM environments where manual file retrieval isn't practical.
