# AI Sales Intelligence Pipeline

A production-ready web application that automatically enriches B2B sales leads using AI. Upload a list of company names, and the app will find their websites, analyze their business using NVIDIA AI, score them as leads, and push structured data to Airtable CRM.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Streamlit UI  │────▶│  Enrichment      │────▶│   Airtable      │
│   (app.py)      │     │  Pipeline        │     │   CRM           │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
       │  Firecrawl  │ │  Firecrawl  │ │  NVIDIA     │
       │  Search     │ │  Scrape     │ │  AI API     │
       │             │ │             │ │             │
       └─────────────┘ └─────────────┘ └─────────────┘
```

## Setup Instructions

### 1. Clone and Install Dependencies

```bash
cd crm-tools-ai
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and fill in your API keys:

```bash
cp .env.example .env
```

Edit `.env` with your actual credentials:

```env
FIRECRAWL_API_KEY=your_firecrawl_api_key_here
NVIDIA_API_KEY=your_nvidia_api_key_here
AIRTABLE_API_KEY=your_airtable_api_key_here
AIRTABLE_BASE_ID=your_base_id_here
AIRTABLE_TABLE_NAME=Leads
```

### 3. Required API Keys

| Service | Where to Get | Free Tier |
|---------|--------------|-----------|
| **Firecrawl** | [firecrawl.dev](https://firecrawl.dev/) | Free tier available |
| **NVIDIA API** | [build.nvidia.com](https://build.nvidia.com/explore/discover) | Free tier available |
| **Airtable** | [airtable.com/create/tokens](https://airtable.com/create/tokens) | Free base available |

### 4. Airtable Setup

Create a new Airtable base with a table named `Leads` containing these fields:

| Field Name | Airtable Type | Notes |
|------------|---------------|-------|
| Company Name | Single line text | Required |
| Website | URL | |
| Summary | Long text | 2-sentence AI summary |
| Industry | Single select | Energy, Technology, Finance, etc. |
| Size | Single select | Employee count range |
| B2B Buyer | Checkbox | True if likely B2B software buyer |
| Lead Score | Number (integer) | 1-10 score |
| Status | Single select | Hot (green), Warm (yellow), Cold (red) |
| Score Reason | Long text | AI explanation of score |
| Enriched At | Date & time | When record was created |

**Pro tip:** Create the Status field with color coding:
- Hot = Green background
- Warm = Yellow background  
- Cold = Red background

### 5. Run Locally

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

## Usage

1. **Upload CSV**: Click the file uploader and select a CSV with company names
2. **Preview**: Review the first 5 companies in the preview section
3. **Enrich All**: Click the button to start the enrichment pipeline
4. **Monitor Progress**: Watch real-time progress as each company is processed
5. **Push to Airtable**: Click "Push Records to Airtable" to save results
6. **Download**: Optionally download results as CSV for backup

## Sample Data

Use the included `sample_companies.csv` file to test the application. It contains 10 energy sector companies:

- Siemens Energy
- Vestas Wind Systems
- Schneider Electric
- ABB Group
- Orsted
- SolarEdge Technologies
- Enphase Energy
- NextEra Energy
- Brookfield Renewable
- EDF Renewables

## Deploy to Streamlit Cloud

1. **Push to GitHub**: Commit your code to a GitHub repository

2. **Go to Streamlit Cloud**: Visit [share.streamlit.io](https://share.streamlit.io)

3. **Create New App**:
   - Click "New App"
   - Select your repository
   - Branch: `main`
   - App file: `app.py`

4. **Set Secrets**: In Streamlit Cloud dashboard, go to Settings → Secrets and add:

```toml
[general]
FIRECRAWL_API_KEY = "your_firecrawl_api_key_here"
NVIDIA_API_KEY = "your_nvidia_api_key_here"
AIRTABLE_API_KEY = "your_airtable_api_key_here"
AIRTABLE_BASE_ID = "your_base_id_here"
```

5. **Deploy**: Click "Deploy" and your app will be live!

## Project Structure

```
crm-tools-ai/
├── .env.example           # Environment variable template
├── .gitignore             # Git ignore rules
├── requirements.txt       # Python dependencies
├── README.md              # This file
├── app.py                 # Main Streamlit application
├── sample_companies.csv   # Test data
├── pipeline/
│   ├── __init__.py
│   ├── search.py          # Firecrawl web search
│   ├── scraper.py         # Firecrawl scraper
│   ├── analyzer.py        # NVIDIA AI analysis
│   └── crm.py             # Airtable integration
└── utils/
    ├── __init__.py
    └── helpers.py         # CSV parsing, retry decorator
```

## Lead Scoring Logic

The AI scores companies 1-10 based on:

- **Industry**: Energy, Technology, Manufacturing score higher
- **Size**: Companies with 51+ employees score higher
- **B2B Buyer**: Companies likely to purchase B2B software score higher

**Status Tags:**
- **Hot** (8-10): High-priority leads ready for immediate outreach
- **Warm** (5-7): Potential leads worth nurturing
- **Cold** (1-4): Low-priority or poor-fit prospects

## Troubleshooting

### "Missing environment variables" error
- Ensure `.env` file exists in the project root
- Check that all 4 variables are set correctly (FIRECRAWL_API_KEY, NVIDIA_API_KEY, AIRTABLE_API_KEY, AIRTABLE_BASE_ID)
- Restart the Streamlit app after changing `.env`

### "No valid companies found" error
- Ensure CSV has a column named `company_name` or data in the first column
- Check that the file is valid CSV format (not Excel)

### Firecrawl rate limit exceeded
- Check Firecrawl dashboard for your plan limits
- Add delays between requests or upgrade plan

### Airtable records not appearing
- Verify table name matches `AIRTABLE_TABLE_NAME` (default: "Leads")
- Check that all field names match exactly (case-sensitive)
- Ensure API token has write permissions

## License

MIT License - See LICENSE file for details.
