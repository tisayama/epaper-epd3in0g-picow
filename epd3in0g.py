# epd3in0g.py
import time
from machine import Pin, SPI

# Display resolution
EPD_WIDTH = 168
EPD_HEIGHT = 400

class EPD:
    def __init__(self, rst_pin, dc_pin, cs_pin, busy_pin):
        self.rst_pin = Pin(rst_pin, Pin.OUT)
        self.dc_pin = Pin(dc_pin, Pin.OUT)
        self.cs_pin = Pin(cs_pin, Pin.OUT)
        self.busy_pin = Pin(busy_pin, Pin.IN, Pin.PULL_DOWN) # BUSYピンはプルダウン
        self.width = EPD_WIDTH
        self.height = EPD_HEIGHT
        self.BLACK = 0x000000  # 00 BGR
        self.WHITE = 0xffffff  # 01
        self.YELLOW = 0x00ffff  # 10
        self.RED = 0x0000ff  # 11

        # Initialize SPI
        self.spi = SPI(0, baudrate=4000000, sck=Pin(18), mosi=Pin(19)) # SPIの初期化

    # Hardware reset
    def reset(self):
        self.rst_pin.value(1)
        time.sleep_ms(200)
        self.rst_pin.value(0)
        time.sleep_ms(2)
        self.rst_pin.value(1)
        time.sleep_ms(200)

    def send_command(self, command):
        self.dc_pin.value(0)
        self.cs_pin.value(0)
        self.spi.write(bytes([command]))
        self.cs_pin.value(1)

    def send_data(self, data):
        self.dc_pin.value(1)
        self.cs_pin.value(0)
        self.spi.write(bytes([data]))
        self.cs_pin.value(1)

    def ReadBusyH(self):
        print("e-Paper busy H")
        while self.busy_pin.value() == 0:  # 0: idle, 1: busy
            time.sleep_ms(5)
        print("e-Paper busy H release")

    def ReadBusyL(self):
        print("e-Paper busy L")
        while self.busy_pin.value() == 1:  # 0: busy, 1: idle
            time.sleep_ms(5)
        print("e-Paper busy L release")

    def TurnOnDisplay(self):
        self.send_command(0x12)  # DISPLAY_REFRESH
        self.send_data(0x01)
        self.ReadBusyH()

        self.send_command(0x02)  # POWER_OFF
        self.send_data(0X00)
        self.ReadBusyH()

    def init(self):
        # EPD hardware init start
        self.reset()

        self.send_command(0x66)
        self.send_data(0x49)
        self.send_data(0x55)
        self.send_data(0x13)
        self.send_data(0x5D)
        self.send_data(0x05)
        self.send_data(0x10)

        self.send_command(0xB0)
        self.send_data(0x00)  # 1 boost

        self.send_command(0x01)
        self.send_data(0x0F)
        self.send_data(0x00)

        self.send_command(0x00)
        self.send_data(0x4F)
        self.send_data(0x6B)

        self.send_command(0x06)
        self.send_data(0xD7)
        self.send_data(0xDE)
        self.send_data(0x12)

        self.send_command(0x61)
        self.send_data(0x00)
        self.send_data(0xA8)
        self.send_data(0x01)
        self.send_data(0x90)

        self.send_command(0x50)
        self.send_data(0x37)

        self.send_command(0x60)
        self.send_data(0x0C)
        self.send_data(0x05)

        self.send_command(0xE3)
        self.send_data(0xFF)

        self.send_command(0x84)
        self.send_data(0x00)
        return 0

    def getbuffer(self, image):
        # into a single byte to transfer to the panel
        buf = bytearray(int(self.width * self.height / 4))
        idx = 0
        for i in range(0, len(image), 4):
            buf[idx] = (image[i] << 6) + (image[i+1] << 4) + (image[i+2] << 2) + image[i+3]
            idx += 1
        return buf

    def display(self, image):
        if self.width % 4 == 0 :
            Width = self.width // 4
        else :
            Width = self.width // 4 + 1
        Height = self.height

        self.send_command(0x04)
        self.ReadBusyH()

        self.send_command(0x10)
        for j in range(0, Height):
            for i in range(0, Width):
                    self.send_data(image[i + j * Width])

        self.TurnOnDisplay()
        
    def Clear(self, color=0x55):
        if self.width % 4 == 0 :
            Width = self.width // 4
        else :
            Width = self.width // 4 + 1
        Height = self.height

        self.send_command(0x04)
        self.ReadBusyH()

        self.send_command(0x10)
        for j in range(0, Height):
            for i in range(0, Width):
                self.send_data(color)

        self.TurnOnDisplay()

    def sleep(self):
        self.send_command(0x02)  # POWER_OFF
        self.send_data(0x00)

        self.send_command(0x07)  # DEEP_SLEEP
        self.send_data(0XA5)

        time.sleep_ms(2000)
        # Picoではpoweroffは不要なので、ここでは何もしない
        # poweroff