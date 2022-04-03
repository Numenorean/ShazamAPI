from copy import copy
from typing import Any, List, Optional

from numpy import array as nparray
from numpy import fft, hanning, log, maximum

from .signature_format import (
    DecodedMessage,
    FrequencyBand,
    FrequencyPeak,
)

HANNING_MATRIX = hanning(2050)[1:-1]  # Wipe trailing and leading zeroes


class RingBuffer(list):  # noqa: WPS600
    def __init__(self, buffer_size: int, default_value: Any = None):
        if default_value is not None:
            super().__init__([copy(default_value) for _ in range(buffer_size)])
        else:
            super().__init__([None] * buffer_size)

        self.position: int = 0
        self.buffer_size: int = buffer_size
        self.num_written: int = 0

    def append(self, value: Any):
        self[self.position] = value
        self.position += 1
        self.position %= self.buffer_size
        self.num_written += 1


class SignatureGenerator(object):
    def __init__(self):
        # Used when storing input that will be processed when requiring to
        # generate a signature:
        self.input_pending_processing: List[int] = []  # Signed 16-bits, 16 KHz mono samples to be processed
        self.samples_processed: int = 0  # Number of samples processed out of "self.input_pending_processing"

        # Used when processing input:
        self.ring_buffer_of_samples: RingBuffer[int] = RingBuffer(buffer_size=2048, default_value=0)
        self.spread_ffts_output: RingBuffer[List[float]] = RingBuffer(buffer_size=256, default_value=[0] * 1025)

        # Used when processing input. Lists of 1025 floats, premultiplied with
        # a Hanning function before being passed through FFT, computed from the
        # ring buffer every new 128 samples:
        self.fft_outputs: RingBuffer[List[float]] = RingBuffer(buffer_size=256, default_value=[float(0)] * 1025)

        # How much data to send to Shazam at once?
        self.MAX_TIME_SECONDS = 3.1
        self.MAX_PEAKS = 255

        # The object that will hold information about the next fingerpring to
        # be produced:
        self.next_signature = DecodedMessage()
        self.next_signature.sample_rate_hz = 16000
        self.next_signature.number_samples = 0
        self.next_signature.frequency_band_to_sound_peaks = {}

    def __next__(self) -> DecodedMessage:
        sig = self.get_next_signature()
        if sig is None:
            raise StopIteration

        return sig

    def __iter__(self):
        return self

    def feed_input(self, s16le_mono_samples: List[int]):
        """
        Add data to be generated a signature for, which will be processed when
        self.get_next_signature() is called. This function expects signed
        16-bit 16 KHz mono PCM samples.
        """
        self.input_pending_processing += s16le_mono_samples

    def get_next_signature(self) -> Optional[DecodedMessage]:
        """
        Consume some of the samples fed to self.feed_input(), and return a
        Shazam signature (DecodedMessage object) to be sent to servers once
        "enough data has been gathered".

        Except if there are no more samples to be consumed, in this case we
        will return None.
        """
        if len(self.input_pending_processing) - self.samples_processed < 128:
            return None

        while (
            len(self.input_pending_processing) - self.samples_processed >= 128
            and (
                self.next_signature.number_samples / self.next_signature.sample_rate_hz < self.MAX_TIME_SECONDS
                or sum(
                    len(peaks) for peaks in self.next_signature.frequency_band_to_sound_peaks.values()
                ) < self.MAX_PEAKS
            )
        ):
            self.process_input(self.input_pending_processing[self.samples_processed:self.samples_processed + 128])
            self.samples_processed += 128

        returned_signature = self.next_signature

        self.next_signature = DecodedMessage()
        self.next_signature.sample_rate_hz = 16000
        self.next_signature.number_samples = 0
        self.next_signature.frequency_band_to_sound_peaks = {}

        self.ring_buffer_of_samples: RingBuffer[int] = RingBuffer(buffer_size=2048, default_value=0)
        self.fft_outputs: RingBuffer[List[float]] = RingBuffer(buffer_size=256, default_value=[float(0)] * 1025)
        self.spread_ffts_output: RingBuffer[List[float]] = RingBuffer(buffer_size=256, default_value=[0] * 1025)

        return returned_signature

    def process_input(self, s16le_mono_samples: List[int]):
        self.next_signature.number_samples += len(s16le_mono_samples)
        for position_of_chunk in range(0, len(s16le_mono_samples), 128):
            self.do_fft(s16le_mono_samples[position_of_chunk:position_of_chunk + 128])
            self.do_peak_spreading_and_recognition()

    def do_fft(self, batch: List[int]):
        """
        Params:
            batch: batch of 128 s16le mono samples
        """
        self.ring_buffer_of_samples[  # noqa: WPS362
            self.ring_buffer_of_samples.position:
            self.ring_buffer_of_samples.position + len(batch)
        ] = batch

        self.ring_buffer_of_samples.position += len(batch)
        self.ring_buffer_of_samples.position %= 2048
        self.ring_buffer_of_samples.num_written += len(batch)

        excerpt_from_ring_buffer: list = (
            self.ring_buffer_of_samples[self.ring_buffer_of_samples.position:]
            + self.ring_buffer_of_samples[:self.ring_buffer_of_samples.position]
        )

        # The premultiplication of the array is for applying a windowing
        # function before the DFT (slighty rounded Hanning without zeros at
        # edges):
        fft_results: nparray = fft.rfft(
            HANNING_MATRIX * excerpt_from_ring_buffer,
        )

        if (
            len(fft_results) != 1025
            or len(excerpt_from_ring_buffer) != 2048
            or len(HANNING_MATRIX) != 2048
        ):
            # TODO: need a better explanation?
            raise RuntimeError('Fast Fourier Transform gone horribly wrong')

        fft_results = (fft_results.real ** 2 + fft_results.imag ** 2) / (1 << 17)
        fft_results = maximum(fft_results, 1e-10)
        self.fft_outputs.append(fft_results)

    def do_peak_spreading_and_recognition(self):
        self.do_peak_spreading()
        if self.spread_ffts_output.num_written >= 46:
            self.do_peak_recognition()

    def do_peak_spreading(self):
        origin_last_fft: List[float] = self.fft_outputs[self.fft_outputs.position - 1]
        spread_last_fft: List[float] = list(origin_last_fft)
        for position in range(1025):
            # Perform frequency-domain spreading of peak values:
            if position < 1023:
                spread_last_fft[position] = max(spread_last_fft[position:position + 3])

            # Perform time-domain spreading of peak values:
            max_value = spread_last_fft[position]
            for former_fft_num in [-1, -3, -6]:
                former_fft_output = self.spread_ffts_output[
                    (self.spread_ffts_output.position + former_fft_num)
                    % self.spread_ffts_output.buffer_size
                ]
                former_fft_output[position] = max_value = max(former_fft_output[position], max_value)

        # Save output locally:
        self.spread_ffts_output.append(spread_last_fft)

    def do_peak_recognition(self):
        fft_minus_46 = self.fft_outputs[(self.fft_outputs.position - 46) % self.fft_outputs.buffer_size]
        fft_minus_49 = self.spread_ffts_output[(self.spread_ffts_output.position - 49) % self.spread_ffts_output.buffer_size]

        for bin_position in range(10, 1015):
            # Ensure that the bin is large enough to be a peak
            if (
                fft_minus_46[bin_position] >= 1 / 64
                and fft_minus_46[bin_position] >= fft_minus_49[bin_position - 1]
            ):
                # Ensure that it is frequency-domain local minimum:
                max_neighbor_in_fft_minus_49 = 0
                for neighbor_offset in (*range(-10, -3, 3), -3, 1, *range(2, 9, 3)):
                    max_neighbor_in_fft_minus_49 = max(
                        fft_minus_49[bin_position + neighbor_offset],
                        max_neighbor_in_fft_minus_49,
                    )

                if fft_minus_46[bin_position] > max_neighbor_in_fft_minus_49:
                    # Ensure that it is a time-domain local minimum:
                    max_neighbor_in_other_adjacent_ffts = max_neighbor_in_fft_minus_49

                    for other_offset in (-53, -45, *range(165, 201, 7), *range(214, 250, 7)):
                        max_neighbor_in_other_adjacent_ffts = max(
                            self.spread_ffts_output[
                                (self.spread_ffts_output.position + other_offset) %
                                self.spread_ffts_output.buffer_size
                            ][bin_position - 1],
                            max_neighbor_in_other_adjacent_ffts,
                        )

                    if fft_minus_46[bin_position] > max_neighbor_in_other_adjacent_ffts:
                        # This is a peak, store the peak:
                        fft_number = self.spread_ffts_output.num_written - 46

                        peak_magnitude = log(max(1 / 64, fft_minus_46[bin_position])) * 1477.3 + 6144
                        peak_magnitude_before = log(max(1 / 64, fft_minus_46[bin_position - 1])) * 1477.3 + 6144
                        peak_magnitude_after = log(max(1 / 64, fft_minus_46[bin_position + 1])) * 1477.3 + 6144

                        peak_variation_1 = peak_magnitude * 2 - peak_magnitude_before - peak_magnitude_after
                        peak_variation_2 = (peak_magnitude_after - peak_magnitude_before) * 32 / peak_variation_1

                        corrected_peak_frequency_bin = bin_position * 64 + peak_variation_2

                        if peak_variation_1 <= 0:
                            # TODO: need a better explanation?
                            raise RuntimeError('peak_variation_1 is not positive')

                        frequency_hz = corrected_peak_frequency_bin * (16000 / 2 / 1024 / 64)
                        if frequency_hz < 250:  # noqa: WPS223, WPS432
                            continue
                        elif frequency_hz < 520:  # noqa: WPS432
                            band = FrequencyBand.band_250_520
                        elif frequency_hz < 1450:  # noqa: WPS432
                            band = FrequencyBand.band_520_1450
                        elif frequency_hz < 3500:  # noqa: WPS432
                            band = FrequencyBand.band_1450_3500
                        elif frequency_hz <= 5500:  # noqa: WPS432
                            band = FrequencyBand.band_3500_5500
                        else:
                            continue

                        if band not in self.next_signature.frequency_band_to_sound_peaks:
                            self.next_signature.frequency_band_to_sound_peaks[band] = []

                        self.next_signature.frequency_band_to_sound_peaks[band].append(
                            FrequencyPeak(
                                fft_number,
                                int(peak_magnitude),
                                int(corrected_peak_frequency_bin),
                                16000,
                            ),
                        )
