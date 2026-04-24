# How to Find Your Airtable Base ID

## Method 1: From Airtable URL (Easiest)

1. Go to https://airtable.com and log in
2. Open your **"Leads"** base/table
3. Look at the browser URL - it will look like:
   ```
   https://airtable.com/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYY/viwZZZZZZZZZZZZZ
   ```
4. Copy the `appXXXXXXXXXXXXXX` part - that's your **Base ID**
5. Update your `.env` file:
   ```
   AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
   ```

## Method 2: From Airtable API Documentation

1. Go to https://airtable.com/create/tokens
2. Click on your base
3. Click "Help" → "API documentation"
4. The Base ID is shown at the top of the page

## Method 3: Create New Base

If you don't have a base yet:
1. Go to https://airtable.com
2. Click "Create a base"
3. Name it "Leads"
4. The Base ID will be in the URL

## ⚠️ Important Notes

- Base IDs start with `app` (e.g., `app1234567890abcd`)
- Your current value `wsp20a2vMWz9SxcpH` looks like a **Workspace ID**, not a Base ID
- Workspace IDs start with `wsp`
- Base IDs start with `app`

## Quick Fix

Replace this in your `.env` file:
```bash
# ❌ Wrong (Workspace ID)
AIRTABLE_BASE_ID=wsp20a2vMWz9SxcpH

# ✅ Correct (Base ID) - Replace with your actual Base ID
AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
```
