import json
import time
import types
import uuid
from io import BytesIO
from typing import Final

import requests
from pydub import AudioSegment

from .algorithm import SignatureGenerator
from .signature_format import DecodedMessage

LANG: Final = 'ru'
REGION: Final = 'RU'
TIME_ZONE: Final = 'Europe/Moscow'
API_URL_TEMPLATE: Final = 'https://amp.shazam.com/discovery/v5/{lang}/{region}/iphone/-/tag/{uuid_a}/{uuid_b}'
HEADERS: Final = types.MappingProxyType({
    'X-Shazam-Platform': 'IPHONE',
    'X-Shazam-AppVersion': '14.1.0',
    'Accept': '*/*',
    'Accept-Language': LANG,
    'Accept-Encoding': 'gzip, deflate',
    'User-Agent': 'Shazam/3685 CFNetwork/1197 Darwin/20.0.0',
})
PARAMS: Final = types.MappingProxyType({
    'sync': 'true',
    'webv3': 'true',
    'sampling': 'true',
    'connected': '',
    'shazamapiversion': 'v3',
    'sharehub': 'true',
    'hubv5minorversion': 'v5.1',
    'hidelb': 'true',
    'video': 'v3',
})


class Shazam(object):
    def __init__(self, song_data: bytes):
        self.song_data = song_data
        self.MAX_TIME_SECONDS = 8

    def recognize_song(self) -> dict:
        self.audio = self.normalizate_audio_data(self.song_data)
        signature_generator = self.create_signature_generator(self.audio)
        while True:
            signature = signature_generator.get_next_signature()
            if not signature:
                break

            results = self.send_recognize_request(signature)
            current_offset = signature_generator.samples_processed / 16000

            yield current_offset, results

    def send_recognize_request(self, sig: DecodedMessage) -> dict:
        data = {
            'timezone': TIME_ZONE,
            'signature': {
                'uri': sig.encode_to_uri(),
                'samplems': int(sig.number_samples / sig.sample_rate_hz * 1000),
            },
            'timestamp': int(time.time() * 1000),
            'context': {},
            'geolocation': {},
        }
        resp = requests.post(
            API_URL_TEMPLATE.format(
                lang=LANG,
                region=REGION,
                uuid_a=str(uuid.uuid4()).upper(),
                uuid_b=str(uuid.uuid4()).upper(),
            ),
            params=PARAMS,
            headers=HEADERS,
            json=data,
        )
        return resp.json()

    def normalizate_audio_data(self, song_data: bytes) -> AudioSegment:
        audio = AudioSegment.from_file(BytesIO(song_data))
        audio = audio.set_sample_width(2)
        audio = audio.set_frame_rate(16000)
        audio = audio.set_channels(1)
        return audio  # noqa: WPS331

    def create_signature_generator(
        self, audio: AudioSegment,
    ) -> SignatureGenerator:
        signature_generator = SignatureGenerator()
        signature_generator.feed_input(audio.get_array_of_samples())
        signature_generator.MAX_TIME_SECONDS = self.MAX_TIME_SECONDS
        if audio.duration_seconds > 12 * 3:
            signature_generator.samples_processed += 16000 * (
                int(audio.duration_seconds / 16) - 6
            )
        return signature_generator
