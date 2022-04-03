import time
import types
import uuid
from io import BytesIO
from typing import BinaryIO, Final, Generator, Tuple, Union

import requests
from pydub import AudioSegment

from .algorithm import SignatureGenerator
from .signature_format import DecodedMessage

LANG: Final = 'ru'
REGION: Final = 'RU'
TIME_ZONE: Final = 'Europe/Moscow'
API_URL_TEMPLATE: Final = (
    'https://amp.shazam.com/discovery/v5'
    + '/{lang}/{region}/iphone/-/tag/{uuid_a}/{uuid_b}'
)
BASE_HEADERS: Final = types.MappingProxyType({
    'X-Shazam-Platform': 'IPHONE',
    'X-Shazam-AppVersion': '14.1.0',
    'Accept': '*/*',
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
NORMALIZED_SAMPLE_WIDTH: Final = 2
NORMALIZED_FRAME_RATE: Final = 16000
NORMALIZED_CHANNELS: Final = 1


class Shazam(object):
    max_time_seconds = 8

    def recognize_song(
        self, audio: Union[bytes, BinaryIO, AudioSegment],
    ) -> Generator[Tuple[float, dict], None, None]:
        audio = self.normalize_audio_data(audio)
        signature_generator = self.create_signature_generator(audio)
        for signature in signature_generator:
            results = self.send_recognize_request(signature)
            current_offset = int(
                signature_generator.samples_processed // NORMALIZED_FRAME_RATE,
            )

            yield current_offset, results

    def normalize_audio_data(
        self, audio: Union[bytes, BinaryIO, AudioSegment],
    ) -> AudioSegment:
        """
        Reads audio to pydub.AudioSegment (if it is not one already), then sets
        sample width, frame rate and channels required by Shazam API.
        """
        if isinstance(audio, bytes):
            audio = AudioSegment.from_file(BytesIO(audio))
        elif not isinstance(audio, AudioSegment):
            audio = AudioSegment.from_file(audio)

        audio = audio.set_sample_width(NORMALIZED_SAMPLE_WIDTH)
        audio = audio.set_frame_rate(NORMALIZED_FRAME_RATE)
        audio = audio.set_channels(NORMALIZED_CHANNELS)
        return audio  # noqa: WPS331

    def create_signature_generator(
        self, audio: AudioSegment,
    ) -> SignatureGenerator:
        """
        Creates a SignatureGenerator instance for given audio segment.
        """
        signature_generator = SignatureGenerator()
        signature_generator.feed_input(audio.get_array_of_samples())
        signature_generator.MAX_TIME_SECONDS = self.max_time_seconds

        # TODO: what 12, 3, 16, 6 mean here? :thinking:
        if audio.duration_seconds > 12 * 3:
            signature_generator.samples_processed += NORMALIZED_FRAME_RATE * (
                int(audio.duration_seconds / 16) - 6
            )

        return signature_generator

    def send_recognize_request(self, sig: DecodedMessage) -> dict:
        data = {
            'timezone': TIME_ZONE,
            'signature': {
                'uri': sig.encode_to_uri(),
                'samplems': int(
                    sig.number_samples / sig.sample_rate_hz * 1000,
                ),
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
            headers={
                **BASE_HEADERS,
                'Accept-Language': LANG,
            },
            json=data,
        )
        return resp.json()
