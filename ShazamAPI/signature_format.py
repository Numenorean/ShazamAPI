import ctypes
import types
from base64 import b64decode, b64encode
from binascii import crc32
from enum import IntEnum
from io import BytesIO
from math import exp, sqrt
from typing import Dict, Final, List

DATA_URI_PREFIX: Final = 'data:audio/vnd.shazam.sig;base64,'

HEADER_SIZE: Final = 48
HEADER_MAGIC1: Final = 0xCAFE2580
HEADER_MAGIC2: Final = 0x94119C00
HEADER_MAGIC3: Final = ((15 << 19) + 0x40000)

SHIFTED_SAMPLE_RATE_FROM_ID: Final = types.MappingProxyType({
    (1 << 27): 8000,
    (2 << 27): 11025,
    (3 << 27): 16000,
    (4 << 27): 32000,
    (5 << 27): 44100,
    (6 << 27): 48000,
})
SHIFTED_SAMPLE_RATE_TO_ID: Final = types.MappingProxyType({
    rate_hz: rate_id
    for rate_id, rate_hz in SHIFTED_SAMPLE_RATE_FROM_ID.items()
})


class FrequencyBand(IntEnum):
    # Enum keys are frequency ranges in Hzs
    band_0_250 = -1  # Nothing above 250 Hz is actually stored
    band_250_520 = 0
    band_520_1450 = 1
    band_1450_3500 = 2
    band_3500_5500 = 3  # This one (3.5 KHz - 5.5 KHz) should not be used in legacy mode


class RawSignatureHeader(ctypes.LittleEndianStructure):
    _pack = True
    _fields_ = [  # noqa: WPS120
        # Fixed HEADER_MAGIC1 - 0xCAFE2580:
        ('magic1', ctypes.c_uint32),

        # CRC-32 for all of the following (so excluding these first 8 bytes):
        ('crc32', ctypes.c_uint32),

        # Total size of the message, minus the size of the current header (which is 48 bytes):
        ('size_minus_header', ctypes.c_uint32),

        # Fixed HEADER_MAGIC2 - 0x94119C00:
        ('magic2', ctypes.c_uint32),

        # Void:
        ('void1', ctypes.c_uint32 * 3),

        # A member of SAMPLE_RATE_TO_ID (usually 3 << 27 for 16000 Hz):
        ('shifted_sample_rate_id', ctypes.c_uint32),

        # Void, or maybe used only in "rolling window" mode?:
        ('void2', ctypes.c_uint32 * 2),

        # int(number_of_samples + sample_rate * 0.24) - As the sample rate is
        # known thanks to the field above, it can be inferred and substracted
        # so that we obtain the number of samples, and from the number of
        # samples and sample rate we can obtain the length of the recording:
        ('number_samples_plus_divided_sample_rate', ctypes.c_uint32),

        # Calculated as ((15 << 19) + 0x40000) = 0x007C0000 - seems pretty
        # constant, may be different in the "SigType.STREAMING" mode:
        ('magic3', ctypes.c_uint32),
    ]


class FrequencyPeak(object):
    def __init__(
        self,
        fft_pass_number: int,
        peak_magnitude: int,
        corrected_peak_frequency_bin: int,
        sample_rate_hz: int,
    ):
        self.fft_pass_number = fft_pass_number
        self.peak_magnitude = peak_magnitude
        self.corrected_peak_frequency_bin = corrected_peak_frequency_bin
        self.sample_rate_hz = sample_rate_hz

    def get_frequency_hz(self) -> float:
        return self.corrected_peak_frequency_bin * (self.sample_rate_hz / 2 / 1024 / 64)
        # ^ Convert back a FFT bin to a frequency, given a 16 KHz sample rate,
        # 1024 useful bins and the multiplication by 64 made before storing the
        # information

    def get_amplitude_pcm(self) -> float:
        return sqrt(
            exp((self.peak_magnitude - 6144) / 1477.3)
            * (1 << 17) / 2,
        ) / 1024
        # ^ Not sure about this calculation but gives small enough numbers

    def get_seconds(self) -> float:
        return (self.fft_pass_number * 128) / self.sample_rate_hz
        # ^ Assume that new FFT bins are emitted every 128 samples, on a
        # standard 16 KHz sample rate basis.


class DecodedMessage(object):
    @classmethod
    def decode_from_binary(cls, data: bytes):
        result = cls()

        buf = BytesIO(data)

        buf.seek(8)
        checksummable_data = buf.read()
        buf.seek(0)

        # Read and check the header

        header = RawSignatureHeader()
        buf.readinto(header)

        # Not checking for HEADER_MAGIC3 because it might be different
        if header.magic1 != HEADER_MAGIC1 or header.magic2 != HEADER_MAGIC2:
            raise ValueError('Wrong magic string specified in header')

        if header.size_minus_header != len(data) - HEADER_SIZE:
            raise ValueError('Wrong size specified in header')

        if crc32(checksummable_data) & 0xFFFFFFFF != header.crc32:
            raise ValueError('Wrong checksum specified in header')

        result.sample_rate_hz: int = SHIFTED_SAMPLE_RATE_FROM_ID[header.shifted_sample_rate_id]
        result.number_samples: int = int(
            header.number_samples_plus_divided_sample_rate
            - result.sample_rate_hz * 0.24,
        )

        # Read the type-length-value sequence that follows the header

        # The first chunk is fixed and has no value, but instead just repeats
        # the length of the message size minus the header:
        if (
            int.from_bytes(buf.read(4), 'little') != 0x40000000
            or int.from_bytes(buf.read(4), 'little') != len(data) - HEADER_SIZE
        ):
            raise ValueError('Unexpected first chunk format')

        # Then, lists of frequency peaks for respective bands follow:
        result.frequency_band_to_sound_peaks: Dict[FrequencyBand, List[FrequencyPeak]] = {}

        while True:
            tlv_header = buf.read(8)
            if not tlv_header:
                break

            frequency_band_id = int.from_bytes(tlv_header[:4], 'little')
            frequency_peaks_size = int.from_bytes(tlv_header[4:], 'little')
            frequency_peaks_padding = -frequency_peaks_size % 4
            frequency_peaks_buf = BytesIO(buf.read(frequency_peaks_size))
            buf.read(frequency_peaks_padding)

            # Decode frequency peaks:
            frequency_band = FrequencyBand(frequency_band_id - 0x60030040)
            fft_pass_number = 0
            result.frequency_band_to_sound_peaks[frequency_band] = []

            while True:
                raw_fft_pass: bytes = frequency_peaks_buf.read(1)
                if not raw_fft_pass:
                    break

                fft_pass_offset: int = raw_fft_pass[0]
                if fft_pass_offset == 0xFF:
                    fft_pass_number = int.from_bytes(frequency_peaks_buf.read(4), 'little')
                    continue
                else:
                    fft_pass_number += fft_pass_offset

                peak_magnitude = int.from_bytes(frequency_peaks_buf.read(2), 'little')
                corrected_peak_frequency_bin = int.from_bytes(frequency_peaks_buf.read(2), 'little')

                result.frequency_band_to_sound_peaks[frequency_band].append(
                    FrequencyPeak(fft_pass_number, peak_magnitude, corrected_peak_frequency_bin, result.sample_rate_hz),
                )

        return result

    @classmethod
    def decode_from_uri(cls, uri: str):
        if not uri.startswith(DATA_URI_PREFIX):
            raise ValueError('Not a valid audio/vnd.shazam.sig data: URI')

        return cls.decode_from_binary(b64decode(uri.replace(DATA_URI_PREFIX, '', 1)))

    def encode_to_json(self) -> dict:
        """
        Encode the current object to a readable JSON format, for debugging
        purposes.
        """
        return {
            'sample_rate_hz': self.sample_rate_hz,
            'number_samples': self.number_samples,
            '_seconds': self.number_samples / self.sample_rate_hz,
            'frequency_band_to_peaks': {
                frequency_band.name.strip('_'): [
                    {
                        'fft_pass_number': frequency_peak.fft_pass_number,
                        'peak_magnitude': frequency_peak.peak_magnitude,
                        'corrected_peak_frequency_bin': frequency_peak.corrected_peak_frequency_bin,
                        '_frequency_hz': frequency_peak.get_frequency_hz(),
                        '_amplitude_pcm': frequency_peak.get_amplitude_pcm(),
                        '_seconds': frequency_peak.get_seconds(),
                    }
                    for frequency_peak in frequency_peaks
                ]
                for frequency_band, frequency_peaks in sorted(self.frequency_band_to_sound_peaks.items())
            },
        }

    def encode_to_binary(self) -> bytes:
        header = RawSignatureHeader()
        header.magic1 = HEADER_MAGIC1
        header.magic2 = HEADER_MAGIC2
        header.shifted_sample_rate_id = SHIFTED_SAMPLE_RATE_TO_ID[self.sample_rate_hz]
        header.magic3 = HEADER_MAGIC3
        header.number_samples_plus_divided_sample_rate = int(self.number_samples + self.sample_rate_hz * 0.24)

        contents_buf = BytesIO()
        for frequency_band, frequency_peaks in sorted(self.frequency_band_to_sound_peaks.items()):
            peaks_buf = BytesIO()
            fft_pass_number = 0

            # NOTE: Correctly filtering and sorting the peaks within the members
            # of "self.frequency_band_to_sound_peaks" is the responsability of the
            # caller

            for frequency_peak in frequency_peaks:
                if frequency_peak.fft_pass_number < fft_pass_number:
                    raise ValueError('frequency_peak.fft_pass_number < fft_pass_number')

                if frequency_peak.fft_pass_number - fft_pass_number >= 255:
                    peaks_buf.write(b'\xff')
                    peaks_buf.write((frequency_peak.fft_pass_number).to_bytes(4, 'little'))
                    fft_pass_number = frequency_peak.fft_pass_number

                peaks_buf.write(bytes([frequency_peak.fft_pass_number - fft_pass_number]))
                peaks_buf.write((frequency_peak.peak_magnitude).to_bytes(2, 'little'))
                peaks_buf.write((frequency_peak.corrected_peak_frequency_bin).to_bytes(2, 'little'))

                fft_pass_number = frequency_peak.fft_pass_number

            contents_buf.write((0x60030040 + int(frequency_band)).to_bytes(4, 'little'))
            contents_buf.write(len(peaks_buf.getvalue()).to_bytes(4, 'little'))
            contents_buf.write(peaks_buf.getvalue())
            contents_buf.write(b'\x00' * (-len(peaks_buf.getvalue()) % 4))

        # Below, write the full message as a binary stream

        header.size_minus_header = len(contents_buf.getvalue()) + 8

        buf = BytesIO()
        buf.write(bytes(header))  # We will rewrite it just after in order to include the final CRC-32

        buf.write((0x40000000).to_bytes(4, 'little'))
        buf.write((len(contents_buf.getvalue()) + 8).to_bytes(4, 'little'))

        buf.write(contents_buf.getvalue())

        buf.seek(8)
        header.crc32 = crc32(buf.read()) & 0xFFFFFFFF
        buf.seek(0)
        buf.write(bytes(header))

        return buf.getvalue()

    def encode_to_uri(self) -> str:
        return DATA_URI_PREFIX + b64encode(self.encode_to_binary()).decode('ascii')
