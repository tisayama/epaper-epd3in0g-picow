# main.py
import time
import machine
import epd3in0g
import io
import urequests  # MicroPython 用の requests ライブラリ
import gc # ガーベジコレクションをインポート
import ujson
import utime
import ubinascii
import ntptime
from rsa.pkcs1 import sign
from rsa.key import PrivateKey
import network
wlan = network.WLAN(network.STA_IF)

# Wi-Fi接続情報
ssid = None
password = None

# bitmap url
url = None

# Pin configuration
RST_PIN = 11
DC_PIN = 21
CS_PIN = 17
BUSY_PIN = 12

# OAuth 2.0 トークンエンドポイント
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

ACCESS_TOKEN = ''


# サービスアカウントのクレデンシャルファイルのパス
CREDENTIALS_FILE = "service-account-key.json"  # 置き換えてください


# Initialize EPD
epd = epd3in0g.EPD(RST_PIN, DC_PIN, CS_PIN, BUSY_PIN)

def load_config():
    
    global credential
    global ssid
    global password
    global url
    global n
    global e
    global d
    global p
    global q
        
    with open("credentials.json", "r") as f:
        credential = ujson.load(f)
        ssid = credential["wifi_ssid"]
        password = credential["wifi_password"]
        url = credential["url"]
                
        n = credential["n"]
        e = credential["e"]
        d = credential["d"]
        p = credential["p"]
        q = credential["q"]

# Wi-Fi接続関数
def connect_wifi():
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect(ssid, password)
        max_wait = 30
        while max_wait > 0 and not wlan.isconnected():
             print('.')
             time.sleep(1)
             max_wait -= 1
        if wlan.isconnected():
            print('network connected:', wlan.ifconfig())
        else:
            print('network connection failed.')
            # ここで処理を中断するかリトライするか決める
            # machine.reset() # 例: 接続失敗ならリセット
    else:
        print('already connected:', wlan.ifconfig())
    return wlan.isconnected()

# --- オーダード・ディザリング設定 ---
# 2x2 Bayer Matrix (0-3の範囲の値)
# このパターンが画面全体で繰り返される
BAYER_MATRIX_2X2 = (
    (0, 2),
    (3, 1)
)

# ディザリング強度係数 (値を大きくするとディザリング効果が強くなるが、ざらつきも増える可能性)
# どの程度の値が良いかは試行錯誤が必要 (例: 16, 32, 48)
DITHER_FACTOR = 32

# --- 色削減処理の改善 ---

# EPDの4色パレット (RGBタプルのタプル)
# C++コードの palette 配列に対応します。
# インデックス 0: 黒, 1: 白, 2: 黄, 3: 赤 の順序が重要です。
# EPDの実際の発色に合わせて微調整が必要な場合があります。
EPD_PALETTE = (
    (0, 0, 0),       # Black (Index 0)
    (255, 255, 255), # White (Index 1)
    (255, 255, 0),   # Yellow (Index 2)
    (255, 0, 0),     # Red (Index 3)
    # 必要であれば他の色を追加したり、RGB値を調整したりできます
    # 例えば、より暗い赤を表現したい場合など (ただし、EPDが表現できる範囲で)
    # (128, 0, 0), # 暗い赤？ (もし使うならインデックス4になる)
)

# C++のdepalette関数を参考に書き換えた色変換関数
def rgb_to_epd_color(r, g, b, palette):
    """
    入力されたRGB値に最も近い色をパレットから探し、そのインデックスを返す。
    距離計算にはRGB各成分の差の二乗和を使用。
    """
    min_diff_sq = 3 * (255**2) + 1 # 差の二乗和の最大値(255^2 * 3)より大きい初期値
    best_index = 0                 # デフォルトは黒インデックス

    # enumerateを使って、インデックスと色タプルを同時に取得
    for index, pal_color in enumerate(palette):
        pal_r, pal_g, pal_b = pal_color

        # RGB各成分の差を計算
        diff_r = r - pal_r
        diff_g = g - pal_g
        diff_b = b - pal_b

        # 差の二乗和を計算 (ユークリッド距離の二乗)
        # math.pow を使うより直接計算する方が速いことが多い
        diff_sq = (diff_r * diff_r) + (diff_g * diff_g) + (diff_b * diff_b)

        # 現在の最小距離よりも小さければ更新
        if diff_sq < min_diff_sq:
            min_diff_sq = diff_sq
            best_index = index
            # 完全に色が一致した場合、それ以上探す必要はない
            if min_diff_sq == 0:
                break

    return best_index # 最も色が近いパレットのインデックス (0, 1, 2, or 3)


# ディザリング対応の色変換関数
def rgb_to_epd_color_dithered(r, g, b, x, y, palette):
    """
    オーダード・ディザリング (2x2 Bayer) を適用し、
    入力されたRGB値に最も近い色をパレットから探し、そのインデックスを返す。
    x, y はピクセルの座標。
    """
    # 1. ピクセル座標(x, y)に基づいてBayerパターンの値を取得
    bayer_value = BAYER_MATRIX_2X2[y % 2][x % 2]

    # 2. 閾値を計算
    # Bayer値(0-3)を正規化(0-0.75)し、強度係数を掛けることで、
    # RGB値に加算/減算するためのオフセットを計算する。
    # 中心化 (bayer_value / 4.0 - 0.5) して、強度を掛ける。
    # 例: bayer=0 -> -0.5*F, bayer=1 -> -0.25*F, bayer=2 -> +0.25*F, bayer=3 -> +0.5*F
    threshold = int(((bayer_value / 3.0) - 0.5) * DITHER_FACTOR) # 3.0で割る方が分布が良いかも

    # 3. 元のRGB値に閾値を加算 (0-255の範囲に収めるクリッピング処理)
    rd = max(0, min(255, r + threshold))
    gd = max(0, min(255, g + threshold))
    bd = max(0, min(255, b + threshold))

    # 4. 閾値適用後のRGB値(rd, gd, bd)に対して、最も近いパレット色を探す
    min_diff_sq = 3 * (255**2) + 1
    best_index = 0

    for index, pal_color in enumerate(palette):
        pal_r, pal_g, pal_b = pal_color
        diff_r = rd - pal_r # ディザリング後の値(rd, gd, bd)と比較
        diff_g = gd - pal_g
        diff_b = bd - pal_b
        diff_sq = (diff_r * diff_r) + (diff_g * diff_g) + (diff_b * diff_b)

        if diff_sq < min_diff_sq:
            min_diff_sq = diff_sq
            best_index = index
            if diff_sq == 0:
                break

    return best_index


def display_bmp_from_url(url, epd):
    buffer = None
    response = None
    # stream 変数は使わず、response.raw か BytesIO を直接使う

    try:
        print(f"Downloading BMP from {url} (stream mode)...")
        headers = {
            "Authorization": "Bearer " + ACCESS_TOKEN,
        }
        # stream=True を使ってレスポンスを取得
        response = urequests.get(url, headers=headers, stream=True)

        if response.status_code == 200:
             print("BMP download successful (stream mode).")
             # response.raw (SSLSocket) をデータソースとして使用
             data_source = response.raw

             # --- BMPヘッダ読み込み ---
             # data_source から直接 read する
             header_chunk1 = data_source.read(14) # ファイルヘッダ読み込み
             if len(header_chunk1) < 14 or header_chunk1[:2] != b'BM':
                 print("Not a valid BMP file or failed to read header.")
                 if response: response.close()
                 return

             bfSize = int.from_bytes(header_chunk1[2:6], 'little')
             bfOffBits = int.from_bytes(header_chunk1[10:14], 'little')

             header_chunk2 = data_source.read(40) # DIBヘッダ (BitmapInfoHeader, 40バイト) を読み込み
             if len(header_chunk2) < 40:
                  print("Failed to read DIB header.")
                  if response: response.close()
                  return

             biSize = int.from_bytes(header_chunk2[0:4], 'little')
             biWidth = int.from_bytes(header_chunk2[4:8], 'little')
             biHeight = int.from_bytes(header_chunk2[8:12], 'little')
             biPlanes = int.from_bytes(header_chunk2[12:14], 'little')
             biBitCount = int.from_bytes(header_chunk2[14:16], 'little')
             biCompression = int.from_bytes(header_chunk2[16:20], 'little')
             # ... 必要なら他のDIBヘッダ情報も読む ...

             # --- ヘッダ読み込み後、ピクセルデータ開始位置までスキップ ---
             header_bytes_read = 14 + 40 # 今読み込んだバイト数 (54バイト)
             # DIBヘッダサイズが40より大きい場合なども考慮が必要だが、まずはこれで試す
             bytes_to_skip = bfOffBits - header_bytes_read
             if bytes_to_skip < 0:
                  print(f"Warning: bfOffBits ({bfOffBits}) seems smaller than header size ({header_bytes_read}).")
                  bytes_to_skip = 0 # スキップしない

             if bytes_to_skip > 0:
                  print(f"Skipping {bytes_to_skip} bytes to reach pixel data...")
                  # seek() を使わずに read() で読み飛ばす
                  chunk_size = 256 # 一度に読み飛ばすサイズ（小さめにする）
                  skipped_total = 0
                  while skipped_total < bytes_to_skip:
                      read_len = min(bytes_to_skip - skipped_total, chunk_size)
                      skipped_data = data_source.read(read_len)
                      if not skipped_data:
                           print(f"Error: Connection closed while skipping {bytes_to_skip} bytes. Skipped only {skipped_total}.")
                           if response: response.close()
                           return
                      skipped_total += len(skipped_data)
                      # print(f"Skipped {len(skipped_data)} bytes, total {skipped_total}/{bytes_to_skip}")
                      # time.sleep_ms(1) # 読み飛ばし中のCPU負荷軽減（必要なら）
                  print(f"Skipped {skipped_total} bytes successfully.")

             # --- 以降の処理 (解像度チェック、バッファ確保、ピクセル処理) ---
             print(f"Image Size: {biWidth}x{biHeight}, BitDepth: {biBitCount}, Offset: {bfOffBits}")

             if biWidth != epd.width or biHeight != epd.height:
                  print(f"Error: BMP size ({biWidth}x{biHeight}) does not match EPD size ({epd.width}x{epd.height}).")
                  if response: response.close()
                  return

             if biBitCount != 24:
                 print(f"Error: Unsupported bit depth: {biBitCount}. Only 24-bit BMP is currently supported.")
                 if response: response.close()
                 return

             buffer_size = (epd.width * epd.height) // 4
             buffer = bytearray(buffer_size)
             print(f"Allocating buffer: {buffer_size} bytes")
             gc.collect()
             print(f"Memory after buffer allocation: {gc.mem_free()} bytes")

             row_size_padded = ((biBitCount * biWidth + 31) // 32) * 4
             row_size_actual = biWidth * 3 # 24bitの場合

             print("Processing pixel data row by row...")
             start_time = time.ticks_ms() # 処理時間計測開始

             for y_epd in range(epd.height):
                 y_bmp = epd.height - 1 - y_epd

                 # --- BMPの1行分のデータを読み込む ---
                 row_data = b''
                 bytes_read = 0
                 try:
                     # row_size_padded 分を読み込むまでループ
                     while bytes_read < row_size_padded:
                          # 小さなチャンクで読むように試す (メモリ効率と安定性のため)
                          read_request_size = min(row_size_padded - bytes_read, 256)
                          chunk = data_source.read(read_request_size)
                          if not chunk:
                              print(f"\nWarning: End of stream reached prematurely at y_bmp={y_bmp}, row byte {bytes_read}/{row_size_padded}")
                              row_data += b'\x00' * (row_size_padded - bytes_read) # 足りない分をゼロ埋め
                              break # ループを抜ける
                          row_data += chunk
                          bytes_read += len(chunk)
                 except Exception as read_e:
                      print(f"\nError reading row data at y_bmp={y_bmp}: {read_e}")
                      # エラーが発生したら処理中断
                      buffer = None # バッファを無効化
                      break

                 if buffer is None: # 上の try でエラーが発生した場合
                      break

                 if len(row_data) < row_size_padded:
                      print(f"\nError: Incomplete row data received for y_bmp={y_bmp}.")
                      buffer = None # バッファを無効化
                      break

                 # --- 1行分のピクセルを処理してバッファに書き込む ---
                 for x_epd in range(epd.width):
                     x_bmp = epd.width - 1 - x_epd
                     pixel_index_in_row = x_bmp * 3
                     if pixel_index_in_row + 2 < row_size_actual: # 実データ範囲内
                        blue = row_data[pixel_index_in_row]
                        green = row_data[pixel_index_in_row + 1]
                        red = row_data[pixel_index_in_row + 2]

                        # ***** ディザリング対応の色変換関数を呼び出す *****
                        # ピクセル座標 (x_epd, y_epd) を渡す
                        epd_color_index = rgb_to_epd_color_dithered(red, green, blue, x_epd, y_epd, EPD_PALETTE)
                        # ************************************************

                     else: # パディング部分
                        epd_color_index = 1 # 白 (パレットのインデックス1)


                     buffer_index = (x_epd + y_epd * epd.width) // 4
                     shift = (3 - (x_epd % 4)) * 2
                     mask = ~(0b11 << shift)
                     buffer[buffer_index] &= mask
                     buffer[buffer_index] |= (epd_color_index << shift)

                 # 定期的に進捗表示とメモリ解放
                 if (y_epd + 1) % 50 == 0:
                      gc.collect()
                      elapsed_ms = time.ticks_diff(time.ticks_ms(), start_time)
                      print(f"Processed line {y_epd + 1}/{epd.height} [{elapsed_ms/1000:.1f}s]. Mem free: {gc.mem_free()}", end='\r')
                      # time.sleep_ms(1) # 必要なら

             # --- ピクセルデータ処理完了 ---
             print("\nPixel data processing finished.") # 改行してプロンプトを綺麗に
             gc.collect()

             # レスポンスを閉じる
             if response:
                  response.close()
                  del response # オブジェクト削除
                  # data_source (response.rawへの参照) も不要になる
                  gc.collect()

             # --- EPDに表示 ---
             if buffer: # バッファが正常に作成された場合のみ表示
                 print("Displaying image on EPD...")
                 epd.display(buffer)
                 print("Image displayed.")
             else:
                 print("Image display skipped due to processing errors.")

        else:
            print(f"Error downloading BMP: Status code {response.status_code}")
            # エラー内容を表示してみる (urequestsが対応していれば)
            try:
                print("Response body:", response.text)
            except:
                pass # textが読めなくても無視
            if response: response.close()

    except MemoryError as e:
        # ... (MemoryErrorハンドリングは同じ) ...
        print(f"##################################################")
        print(f"Memory Error occurred: {e}")
        print(f"Memory Info: alloc={gc.mem_alloc()}, free={gc.mem_free()}")
        print(f"##################################################")
        if buffer: del buffer
        if response: response.close()
        gc.collect()

    except Exception as e:
        # ... (その他のエラーハンドリング) ...
        print(f"An unexpected error occurred: {e}")
        import sys
        sys.print_exception(e)
        if buffer: del buffer
        if response: response.close()
        gc.collect()

def time_sync():
    global last_ntp_sync
    ntptime.host = 'time.cloudflare.com'
    ntptime.timeout = 10
    ntptime.settime()
    
    print("ntp synced")
    last_ntp_sync = utime.time()

def get_next_runtime():
    """
    現在時刻から次の実行時刻(01:20, 05:20, 11:20, 17:20)までの秒数を計算する
    Returns:
        int: 次の実行時刻までの待機秒数
    """
    current_time = utime.time() + (9 * 3600)  # UTC+9へ調整
    current_day = current_time // 86400  # 現在の日付(エポックからの日数)
    day_seconds = current_time % 86400   # 当日の経過秒数

    # 実行時刻のリスト（1日の秒数に変換）
    run_times = [
        (1 * 3600) + (20 * 60),   # 01:20
        (5 * 3600) + (20 * 60),   # 05:20
        (11 * 3600) + (20 * 60),  # 11:20
        (17 * 3600) + (20 * 60),  # 17:20
    ]

    # 今日の残りの実行時刻から探す
    for run_time in run_times:
        if day_seconds < run_time:
            return run_time - day_seconds

    # 今日の実行時刻をすべて過ぎている場合、翌日の最初の時刻まで
    return (86400 - day_seconds) + run_times[0]

def renew_token():
    global ACCESS_TOKEN
    
    ACCESS_TOKEN = get_access_token(CREDENTIALS_FILE)        
        
def get_access_token(credentials_file):
    """サービスアカウントのクレデンシャルファイルを使用してアクセストークンを取得する"""
    with open(credentials_file, "r") as f:
        credentials = ujson.load(f)


    print("generating jwt")
    headers = {"Content-Type": "application/json"} # application/x-www-form-urlencoded
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": generate_jwt_assertion(credentials),
    }

    print("request access token")
    response = urequests.post(TOKEN_ENDPOINT, headers=headers, data=ujson.dumps(data), timeout=30)

    if response.status_code == 200:
        access_token = response.json()["access_token"]
        response.close()
        return access_token
    else:
        print(
            f"Error getting access token: {response.status_code} {response.text}"
        )
        response.close()
        return None
    
def generate_jwt_assertion(credentials):
    """JWTアサーションを生成する"""
    # ペイロード
    now = int(utime.time())
    payload = {
        "iss": credentials["client_email"],
        "sub": credentials["client_email"],
        "aud": TOKEN_ENDPOINT,
        "iat": now,
        "exp": now + 3600,  # 有効期限: 1時間
        "scope": "https://www.googleapis.com/auth/devstorage.read_only",
    }
    
    return jwt_encode(payload, credentials["private_key"])

def _to_b64url(data):
    return (
        ubinascii.b2a_base64(data)
        .rstrip(b"\n")
        .rstrip(b"=")
        .replace(b"+", b"-")
        .replace(b"/", b"_")
    )


def _from_b64url(data):
    return ubinascii.a2b_base64(data.replace(b"-", b"+").replace(b"_", b"/") + b"===")


class exceptions:
    class PyJWTError(Exception):
        pass

    class InvalidTokenError(PyJWTError):
        pass

    class InvalidAlgorithmError(PyJWTError):
        pass

    class InvalidSignatureError(PyJWTError):
        pass

    class ExpiredTokenError(PyJWTError):
        pass

def jwt_encode(payload, pem_content, algorithm="RS256"):
    global n
    global e
    global d
    global p
    global q
    
    if algorithm != "RS256":
        raise exceptions.InvalidAlgorithmError()

    key = PrivateKey(n, e, d, p, q)
    
    header = _to_b64url(ujson.dumps({"typ": "JWT", "alg": algorithm}).encode())
    payload = _to_b64url(ujson.dumps(payload).encode())
    message = header + b"." + payload
    signature = _to_b64url(sign(message, key, "SHA-256"))
    return (header + b"." + payload + b"." + signature).decode()

def is_active_time():
    """
    現在時刻が起動時間帯（各時刻から45分間）かどうかを判定する
    Returns:
        bool: 起動時間帯であればTrue、それ以外はFalse
    """
    current_time = utime.time() + (9 * 3600)  # UTC+9へ調整
    day_seconds = current_time % 86400   # 当日の経過秒数

    # 各実行時刻（秒）とその45分後の時刻
    active_periods = [
        ((1 * 3600) + (20 * 60), (1 * 3600) + (65 * 60)),   # 01:20-02:05
        ((5 * 3600) + (20 * 60), (5 * 3600) + (65 * 60)),   # 05:20-06:05
        ((11 * 3600) + (20 * 60), (11 * 3600) + (65 * 60)), # 11:20-12:05
        ((17 * 3600) + (20 * 60), (17 * 3600) + (65 * 60)), # 17:20-18:05
    ]

    # いずれかの時間帯に該当するかチェック
    for start, end in active_periods:
        if start <= day_seconds < end:
            return True   
    return False

# main関数
def main():
    status_led = machine.Pin('LED', machine.Pin.OUT)
    status_led.value(1)
    
    try:
        print("Initializing EPD...")
        epd.init()
        
        load_config()
        
        print("Connecting to WiFi...")
        if not connect_wifi():
            print("WiFi connection failed.")

            machine.reset()
            
        time_sync()

        if is_active_time():
            renew_token()
            
            gc.collect()
            print(f"Initial memory free: {gc.mem_free()} bytes")
                
            gc.collect()
            print(f"Memory after EPD init/clear: {gc.mem_free()} bytes")

            # BMP表示関数を呼び出す
            display_bmp_from_url(url, epd)

            gc.collect()
            print(f"Memory free after display attempt: {gc.mem_free()} bytes")
        else:
            print("Not in active time. Skipping display.")

        wlan.disconnect()
        wlan.active(False)
        
        
        machine.Pin(23, machine.Pin.OUT).low()
        wait_time = get_next_runtime() * 1000  # ミリ秒に変換
        if wait_time > 1800 * 1000:
            wait_time = 1800 * 1000
        print(f"Sleeping for {wait_time / 1000} seconds until next run.")
        
        print("Putting EPD to sleep.")
        epd.sleep()
        print("EPD is sleeping.")
        
        status_led.value(0)
        
        machine.deepsleep(wait_time)
    except Exception:
        machine.reset()

if __name__ == "__main__":
    main()