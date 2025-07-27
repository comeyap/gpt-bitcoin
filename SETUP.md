# Environment Setup Guide

## Python Environment Setup for gpt-bitcoin

### Prerequisites
- Python 3.9.6
- pip
- Chrome browser (for Selenium)

### Method 1: Using requirements.txt (Recommended)
```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### Method 2: Using conda
```bash
# Create environment from yml file
conda env create -f environment.yml
conda activate gpt-bitcoin
```

### Method 3: Manual installation
```bash
pip install pyupbit pandas pandas-ta openai schedule requests python-dotenv selenium pillow webdriver-manager streamlit
```

### Environment Variables
Create a `.env` file with:
```
OPENAI_API_KEY=your_openai_api_key
UPBIT_ACCESS_KEY=your_upbit_access_key
UPBIT_SECRET_KEY=your_upbit_secret_key
SERPAPI_API_KEY=your_serpapi_api_key
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver  # Optional
```

### ChromeDriver Setup
1. Install Chrome browser
2. Download ChromeDriver from https://chromedriver.chromium.org/
3. Place in /usr/local/bin/ or update CHROMEDRIVER_PATH in .env

### Verification
```bash
python autotrade_v3.py  # Should run without errors
```
