from pydub import AudioSegment
from io import BytesIO
import requests
import uuid
import time
import json


from .algorithm import SignatureGenerator
from .signature_format import DecodedMessage

LANG = 'ru'
TIME_ZONE = 'Europe/Moscow'
API_URL = 'https://amp.shazam.com/discovery/v5/ru/RU/iphone/-/tag/%s/%s?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3&sharehub=true&hubv5minorversion=v5.1&hidelb=true&video=v3'
HEADERS = {
    "X-Shazam-Platform": "IPHONE",
    "X-Shazam-AppVersion": "14.1.0",
    "Accept": "*/*",
    "Accept-Language": LANG,
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "Shazam/3685 CFNetwork/1197 Darwin/20.0.0"
}


class Shazam:
    def __init__(self, songData: bytes):
        self.songData = songData
        self.MAX_TIME_SECONDS = 8

    def recognizeSong(self) -> dict:
        self.audio = self.normalizateAudioData(self.songData)
        signatureGenerator = self.createSignatureGenerator(self.audio)
        while True:
        
            signature = signatureGenerator.get_next_signature()
            if not signature:
                break
            
            results = self.sendRecognizeRequest(signature)
            currentOffset = signatureGenerator.samples_processed / 16000
            
            yield currentOffset, results
    
    def sendRecognizeRequest(self, sig: DecodedMessage) -> dict:
        data = {
            'timezone': TIME_ZONE,
            'signature': {
                'uri': sig.encode_to_uri(),
                'samplems':int(sig.number_samples / sig.sample_rate_hz * 1000)
                },
            'timestamp': int(time.time() * 1000),
            'context': {},
            'geolocation': {}
                }
        r = requests.post(
            API_URL % (str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()), 
            headers=HEADERS,
            json=data
        )
        return r.json()
    
    def normalizateAudioData(self, songData: bytes) -> AudioSegment:
        audio = AudioSegment.from_file(BytesIO(songData))
    
        audio = audio.set_sample_width(2)
        audio = audio.set_frame_rate(16000)
        audio = audio.set_channels(1)
        return audio
    
    def createSignatureGenerator(self, audio: AudioSegment) -> SignatureGenerator:
        signature_generator = SignatureGenerator()
        signature_generator.feed_input(audio.get_array_of_samples())
        signature_generator.MAX_TIME_SECONDS = self.MAX_TIME_SECONDS
        if audio.duration_seconds > 12 * 3:
            signature_generator.samples_processed += 16000 * (int(audio.duration_seconds / 16) - 6)
        return signature_generator
