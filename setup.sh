#!/bin/bash

# Environment Setup Script for gpt-bitcoin
# Run this script on the new MacBook

set -e  # Exit on any error

echo "🚀 Setting up gpt-bitcoin environment..."

# Check if Python 3.9 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+ first."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✅ Found Python $PYTHON_VERSION"

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv .venv

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "⬆️ Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📥 Installing Python packages..."
pip install -r requirements.txt

# Check if Chrome is installed
if ! command -v google-chrome &> /dev/null && ! command -v chrome &> /dev/null; then
    echo "⚠️ Chrome browser not found. Please install Chrome for Selenium functionality."
else
    echo "✅ Chrome browser found"
fi

# Check for ChromeDriver
if ! command -v chromedriver &> /dev/null; then
    echo "📥 Installing ChromeDriver..."
    # Install using webdriver-manager (automatic)
    python -c "from selenium import webdriver; from selenium.webdriver.chrome.service import Service; from webdriver_manager.chrome import ChromeDriverManager; print('ChromeDriver installed:', ChromeDriverManager().install())"
else
    echo "✅ ChromeDriver found"
fi

# Create .env template if not exists
if [ ! -f .env ]; then
    echo "📝 Creating .env template..."
    cat > .env << EOF
OPENAI_API_KEY=your_openai_api_key_here
UPBIT_ACCESS_KEY=your_upbit_access_key_here
UPBIT_SECRET_KEY=your_upbit_secret_key_here
SERPAPI_API_KEY=your_serpapi_api_key_here
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
EOF
    echo "⚠️ Please update .env file with your actual API keys"
else
    echo "✅ .env file already exists"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "1. Update .env file with your API keys"
echo "2. Activate the environment: source .venv/bin/activate"
echo "3. Test the setup: python autotrade_v3.py"
echo ""
