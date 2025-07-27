import os
from dotenv import load_dotenv
load_dotenv()
import pyupbit
import pandas as pd
import pandas_ta as ta
import json
from openai import OpenAI
import schedule
import time
import requests
from datetime import datetime
import sqlite3
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import base64
import traceback
from PIL import Image
from io import BytesIO
import config  # 설정 파일 import
# Setup
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))

# API 키 확인 (보안상 실제 키는 출력하지 않음)
if os.getenv("UPBIT_ACCESS_KEY") and os.getenv("UPBIT_SECRET_KEY"):
    print("Upbit API keys loaded successfully")
else:
    print("Warning: Upbit API keys not found")

def initialize_db(db_path=config.DB_PATH):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                decision TEXT,
                percentage REAL,
                reason TEXT,
                btc_balance REAL,
                krw_balance REAL,
                btc_avg_buy_price REAL,
                btc_krw_price REAL
            );
        ''')
        conn.commit()

def save_decision_to_db(decision, current_status):
    db_path = config.DB_PATH
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
    
        # Parsing current_status from JSON to Python dict
        status_dict = json.loads(current_status)
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        
        # Preparing data for insertion
        data_to_insert = (
            decision.get('decision'),
            decision.get('percentage', 100),  # Defaulting to 100 if not provided
            decision.get('reason', ''),  # Defaulting to an empty string if not provided
            status_dict.get('btc_balance'),
            status_dict.get('krw_balance'),
            status_dict.get('btc_avg_buy_price'),
            current_price
        )
        
        # Inserting data into the database
        cursor.execute('''
            INSERT INTO decisions (timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price)
            VALUES (datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?)
        ''', data_to_insert)
    
        conn.commit()

def fetch_last_decisions(db_path=config.DB_PATH, num_decisions=config.DEFAULT_DECISIONS_LIMIT):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price FROM decisions
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (num_decisions,))
        decisions = cursor.fetchall()

        if decisions:
            formatted_decisions = []
            for decision in decisions:
                # Converting timestamp to milliseconds since the Unix epoch
                ts = datetime.strptime(decision[0], "%Y-%m-%d %H:%M:%S")
                ts_millis = int(ts.timestamp() * 1000)
                
                formatted_decision = {
                    "timestamp": ts_millis,
                    "decision": decision[1],
                    "percentage": decision[2],
                    "reason": decision[3],
                    "btc_balance": decision[4],
                    "krw_balance": decision[5],
                    "btc_avg_buy_price": decision[6]
                }
                formatted_decisions.append(str(formatted_decision))
            return "\n".join(formatted_decisions)
        else:
            return "No decisions found."

def get_current_status():
    orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")
    current_time = orderbook['timestamp']
    btc_balance = 0
    krw_balance = 0
    btc_avg_buy_price = 0
    
    try:
        balances = upbit.get_balances()
        for b in balances:
            if b['currency'] == "BTC":
                btc_balance = float(b['balance'])
                btc_avg_buy_price = float(b['avg_buy_price'])
            if b['currency'] == "KRW":
                krw_balance = float(b['balance'])
    except Exception as e:
        print(f"Error getting balances: {e}")

    current_status = {'current_time': current_time, 'orderbook': orderbook, 'btc_balance': btc_balance, 'krw_balance': krw_balance, 'btc_avg_buy_price': btc_avg_buy_price}
    return json.dumps(current_status)


def fetch_and_prepare_data():
    # Fetch data
    df_daily = pyupbit.get_ohlcv("KRW-BTC", "day", count=30)
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)

    # Define a helper function to add indicators
    def add_indicators(df):
        # Moving Averages
        df['SMA_10'] = ta.sma(df['close'], length=10)
        df['EMA_10'] = ta.ema(df['close'], length=10)

        # RSI
        df['RSI_14'] = ta.rsi(df['close'], length=14)

        # Stochastic Oscillator
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
        df = df.join(stoch)

        # MACD
        ema_fast = df['close'].ewm(span=12, adjust=False).mean()
        ema_slow = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

        # Bollinger Bands
        df['Middle_Band'] = df['close'].rolling(window=20).mean()
        # Calculate the standard deviation of closing prices over the last 20 days
        std_dev = df['close'].rolling(window=20).std()
        # Calculate the upper band (Middle Band + 2 * Standard Deviation)
        df['Upper_Band'] = df['Middle_Band'] + (std_dev * 2)
        # Calculate the lower band (Middle Band - 2 * Standard Deviation)
        df['Lower_Band'] = df['Middle_Band'] - (std_dev * 2)

        return df

    # Add indicators to both dataframes
    df_daily = add_indicators(df_daily)
    df_hourly = add_indicators(df_hourly)

    combined_df = pd.concat([df_daily, df_hourly], keys=['daily', 'hourly'])
    combined_data = combined_df.to_json(orient='split')

    return json.dumps(combined_data)

def get_news_data():
    ### Get news data from SERPAPI
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    if not serpapi_key:
        print("SERPAPI_API_KEY not found in environment variables")
        return "No news data available."
    
    url = "https://serpapi.com/search.json?engine=google_news&q=btc&api_key=" + serpapi_key

    result = "No news data available."

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # HTTP 에러 발생 시 예외 발생
        
        data = response.json()
        
        if 'news_results' not in data:
            print("No news_results in response")
            print("Response keys:", list(data.keys()))
            return result
            
        news_results = data['news_results']
        simplified_news = []
        
        for news_item in news_results:
            try:
                # Check if this news item contains 'stories'
                if 'stories' in news_item:
                    for story in news_item['stories']:
                        try:
                            # 날짜 형식이 다를 수 있으므로 여러 형식 시도
                            date_str = story.get('date', '')
                            if date_str:
                                # 여러 날짜 형식 처리
                                try:
                                    timestamp = int(datetime.strptime(date_str, '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                                except ValueError:
                                    try:
                                        timestamp = int(datetime.strptime(date_str, '%m/%d/%Y').timestamp() * 1000)
                                    except ValueError:
                                        timestamp = int(datetime.now().timestamp() * 1000)
                            else:
                                timestamp = int(datetime.now().timestamp() * 1000)
                                
                            simplified_news.append((
                                story.get('title', 'No title'),
                                story.get('source', {}).get('name', 'Unknown source'),
                                timestamp
                            ))
                        except Exception as e:
                            print(f"Error processing story: {e}")
                else:
                    # Process news items that are not categorized under stories
                    try:
                        date_str = news_item.get('date', '')
                        if date_str:
                            try:
                                timestamp = int(datetime.strptime(date_str, '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                            except ValueError:
                                try:
                                    timestamp = int(datetime.strptime(date_str, '%m/%d/%Y').timestamp() * 1000)
                                except ValueError:
                                    timestamp = int(datetime.now().timestamp() * 1000)
                        else:
                            timestamp = int(datetime.now().timestamp() * 1000)
                            
                        simplified_news.append((
                            news_item.get('title', 'No title'),
                            news_item.get('source', {}).get('name', 'Unknown source'),
                            timestamp
                        ))
                    except Exception as e:
                        print(f"Error processing news item: {e}")
            except Exception as e:
                print(f"Error processing news item: {e}")
                
        result = str(simplified_news[:10])  # 최대 10개의 뉴스만 반환
        print(f"Successfully fetched {len(simplified_news)} news items")
        
    except requests.RequestException as e:
        print(f"Error fetching news data (HTTP): {e}")
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON response: {e}")
    except Exception as e:
        print(f"Error fetching news data: {e}")

    return result

def fetch_fear_and_greed_index(limit=1, date_format=''):
    """
    Fetches the latest Fear and Greed Index data.
    Parameters:
    - limit (int): Number of results to return. Default is 1.
    - date_format (str): Date format ('us', 'cn', 'kr', 'world'). Default is '' (unixtime).
    Returns:
    - dict or str: The Fear and Greed Index data in the specified format.
    """
    base_url = "https://api.alternative.me/fng/"
    params = {
        'limit': limit,
        'format': 'json',
        'date_format': date_format
    }
    response = requests.get(base_url, params=params)
    myData = response.json()['data']
    resStr = ""
    for data in myData:
        resStr += str(data)
    return resStr

def get_current_base64_image():
    screenshot_path = config.SCREENSHOT_PATH
    driver = None
    try:
        # Set up Chrome options for headless mode
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920x1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        # ChromeDriver 경로를 환경 변수에서 가져오거나 기본값 사용
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
        service = Service(chromedriver_path)

        # Initialize the WebDriver with the specified options
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # User-Agent 설정
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Navigate to the desired webpage
        driver.get("https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC")

        # Wait for the page to load completely
        wait = WebDriverWait(driver, 20)
        
        # 페이지가 완전히 로드될 때까지 대기
        time.sleep(5)

        try:
            # 시간대 메뉴 클릭 (CSS 선택자 사용)
            period_menu = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "cq-menu.ciq-period")))
            driver.execute_script("arguments[0].click();", period_menu)
            time.sleep(2)

            # 1시간 옵션 클릭
            one_hour_option = wait.until(EC.element_to_be_clickable((By.XPATH, "//cq-item[@stxtap=\"Layout.setPeriodicity(1,60,'minute')\"]")))
            driver.execute_script("arguments[0].click();", one_hour_option)
            time.sleep(2)
            
            print("Successfully set 1-hour timeframe")
        except Exception as e:
            print(f"Warning: Could not set timeframe: {e}")

        # MACD 지표 추가 시도 (실패해도 계속 진행)
        try:
            # 지표 메뉴 클릭
            studies_menu = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "cq-menu.ciq-studies")))
            driver.execute_script("arguments[0].click();", studies_menu)
            time.sleep(3)

            # 모든 지표 아이템을 가져와서 MACD 찾기
            study_items = driver.find_elements(By.CSS_SELECTOR, "cq-studies cq-item")
            
            macd_indicator = None
            for item in study_items:
                if "MACD" in item.text:
                    macd_indicator = item
                    break
            
            if macd_indicator:
                # 스크롤하여 MACD가 보이도록 함
                driver.execute_script("arguments[0].scrollIntoView(true);", macd_indicator)
                time.sleep(1)
                
                # MACD 지표 클릭
                driver.execute_script("arguments[0].click();", macd_indicator)
                time.sleep(3)
                print("Successfully added MACD indicator")
            else:
                print("Warning: MACD indicator not found")
        except Exception as e:
            print(f"Warning: Could not add MACD indicator: {e}")

        # 차트 영역이 로드될 때까지 대기
        time.sleep(3)

        # 스크린샷 촬영
        png = driver.get_screenshot_as_png()
        img = Image.open(BytesIO(png))
        img.save(screenshot_path)
        
        print("Chart screenshot taken successfully")
        
    except Exception as e:
        traceback.print_exc()
        print(f"Error making current image: {e}")
        return ""
    finally:
        # Close the browser
        if driver:
            driver.quit()
        
        # 스크린샷 파일이 존재하면 base64로 인코딩하여 반환
        try:
            with open(screenshot_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except FileNotFoundError:
            print("Screenshot file not found")
            return ""

def get_instructions(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            instructions = file.read()
        return instructions
    except FileNotFoundError:
        print("File not found.")
    except Exception as e:
        print("An error occurred while reading the file:", e)

def analyze_data_with_gpt4(news_data, data_json, last_decisions, fear_and_greed, current_status, current_base64_image):
    instructions_path = "instructions_v3.md"
    try:
        instructions = get_instructions(instructions_path)
        if not instructions:
            print("No instructions found.")
            return None
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": news_data},
                {"role": "user", "content": data_json},
                {"role": "user", "content": last_decisions},
                {"role": "user", "content": fear_and_greed},
                {"role": "user", "content": current_status},
                {"role": "user", "content": [{"type": "image_url","image_url": {"url": f"data:image/jpeg;base64,{current_base64_image}"}}]}
            ],
            response_format={"type":"json_object"}
        )
        advice = response.choices[0].message.content
        return advice
    except Exception as e:
        print(f"Error in analyzing data with GPT-4: {e}")
        return None

def execute_buy(percentage):
    print("Attempting to buy BTC with a percentage of KRW balance...")
    try:
        krw_balance = float(upbit.get_balance("KRW"))
        amount_to_invest = krw_balance * (percentage / 100)
        if amount_to_invest > config.MIN_ORDER_AMOUNT:  # 최소 주문 금액 확인
            result = upbit.buy_market_order("KRW-BTC", amount_to_invest * config.FEE_RATE)  # 수수료 적용
            print("Buy order successful:", result)
        else:
            print(f"Order amount ({amount_to_invest}) is below minimum threshold ({config.MIN_ORDER_AMOUNT})")
    except Exception as e:
        print(f"Failed to execute buy order: {e}")

def execute_sell(percentage):
    print("Attempting to sell a percentage of BTC...")
    try:
        btc_balance = float(upbit.get_balance("BTC"))
        amount_to_sell = btc_balance * (percentage / 100)
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        if current_price * amount_to_sell > config.MIN_ORDER_AMOUNT:  # 최소 주문 금액 확인
            result = upbit.sell_market_order("KRW-BTC", amount_to_sell)
            print("Sell order successful:", result)
        else:
            print(f"Order amount ({current_price * amount_to_sell}) is below minimum threshold ({config.MIN_ORDER_AMOUNT})")
    except Exception as e:
        print(f"Failed to execute sell order: {e}")

def make_decision_and_execute():
    print("Making decision and executing...")
    try:
        news_data = get_news_data()
        data_json = fetch_and_prepare_data()
        last_decisions = fetch_last_decisions()
        fear_and_greed = fetch_fear_and_greed_index(limit=config.FEAR_GREED_LIMIT)
        current_status = get_current_status()
        current_base64_image = get_current_base64_image()
    except Exception as e:
        traceback.print_exc()
        print(f"Error: {e}")
    else:
        max_retries = config.MAX_RETRIES
        retry_delay_seconds = config.RETRY_DELAY_SECONDS
        decision = None
        for attempt in range(max_retries):
            try:
                advice = analyze_data_with_gpt4(news_data, data_json, last_decisions, fear_and_greed, current_status, current_base64_image)
                decision = json.loads(advice)
                break
            except Exception as e:
                print(f"JSON parsing failed: {e}. Retrying in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds)
                print(f"Attempt {attempt + 2} of {max_retries}")
        if not decision:
            print("Failed to make a decision after maximum retries.")
            return
        else:
            try:
                percentage = decision.get('percentage', 100)
                print("decision : ", decision)
                if decision.get('decision') == "buy":
                    execute_buy(percentage)
                elif decision.get('decision') == "sell":
                    execute_sell(percentage)
                
                save_decision_to_db(decision, current_status)
            except Exception as e:
                print(f"Failed to execute the decision or save to DB: {e}")

if __name__ == "__main__":
    # initialize_db()
    # testing
    # schedule.every().minute.do(make_decision_and_execute)
    make_decision_and_execute()

    # Schedule the task to run at 00:01
    # schedule.every().day.at("00:01").do(make_decision_and_execute)

    # Schedule the task to run at 08:01
    # schedule.every().day.at("08:01").do(make_decision_and_execute)

    # Schedule the task to run at 16:01
    # schedule.every().day.at("16:01").do(make_decision_and_execute)

    while True:
        schedule.run_pending()
        time.sleep(1)