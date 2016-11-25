#!/usr/bin/env python2
# -*- coding: utf-8 -*-
""" tuner.py: a GNU Radio based FM tuner with RDS support """
# Copyright (C) 2016 Elad Alfassa <elad@fedoraproject.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function, unicode_literals

import cmath
import osmosdr
import pmt
import rds  # https://github.com/bastibl/gr-rds
import rtlsdr  # For device enumeration only
from numbers import Number
from gnuradio.filter import firdes
from gnuradio import analog
from gnuradio import audio
from gnuradio import blocks
from gnuradio import digital
from gnuradio import gr
import gnuradio.filter as grf  # shorter, and avoids overriding a builtin


class MonoFMRceiver(gr.top_block):
    """ A GNU Radio based mono FM receiver with RDS support """

    def __init__(self, sdr=0):
        """ Initialize the receiver, use the ``sdr`` parameter if you have more than one SDR connected """
        gr.top_block.__init__(self)
        # lower sample rate means lower CPU usage, but idk how low you can
        # push it and still have the audio sound fine.
        # Practical maximum for RTL-SDR is 2.4M
        self._sample_rate = sample_rate = 2400000
        self._volume = _volume = 1
        self._frequency = None
        self._gain = 49.6
        self.cutoff_freq = cutoff_freq = 100e3
        self.bb_decim = 4
        self.rds_adapter = RDSAdapter()

        # Initialize SDR source
        self.rtlsdr_source = osmosdr.source(args=b"numchan=1 rtl=%s" % sdr)
        self.rtlsdr_source.set_sample_rate(sample_rate)
        self.rtlsdr_source.set_gain(self._gain)
        self.frequency = 87600000
        # self.gain = 'auto'

        # Audio stuff
        self.audio_resampler = grf.rational_resampler_fff(interpolation=49,
                                                          decimation=50,
                                                          taps=None,
                                                          fractional_bw=None)
        self.rational_resampler = grf.rational_resampler_ccc(
                                                             interpolation=500000,
                                                             decimation=sample_rate,
                                                             taps=None,
                                                             fractional_bw=None)

        self.low_pass_filter = grf.fir_filter_ccf(1,
                                                  firdes.low_pass(2, 500*1000,
                                                                  cutoff_freq,
                                                                  7000,
                                                                  firdes.WIN_HAMMING,
                                                                  6.76))
        self.volume_multiplier = blocks.multiply_const_vff((_volume,))
        self.audio_sink = audio.sink(48000, b'', True)

        # RDS stuff
        self.rds_parser = rds.parser(False, False)
        self.rds_decoder = rds.decoder(False, False)

        # Yes, there are two FM decoders here.
        # I couldn't get RDS to work with only one.
        self.wfm_rcv_audio = analog.wfm_rcv(quad_rate=250e3,
                                            audio_decimation=10)
        self.wfm_rcv_rds = analog.wfm_rcv(quad_rate=sample_rate,
                                          audio_decimation=self.bb_decim)

        self._connect_rds_flow()

        self.connect(self.rtlsdr_source, self.rational_resampler,
                     self.low_pass_filter, self.wfm_rcv_audio,
                     self.audio_resampler, self.volume_multiplier,
                     self.audio_sink)

    def _connect_rds_flow(self):
        xlate_bandwidth = 100000
        audio_decim = 5
        sample_rate = self._sample_rate
        baseband_rate = sample_rate/self.bb_decim
        freq_offset = 0

        cosine_filter = grf.fir_filter_ccf(1, firdes.root_raised_cosine(1,
                                           baseband_rate/audio_decim, 2375, 1,
                                           100))

        fir0 = grf.freq_xlating_fir_filter_ccc(1,
                                               firdes.low_pass(1,
                                                               sample_rate,
                                                               xlate_bandwidth,
                                                               100000),
                                               freq_offset, sample_rate)
        fir1 = grf.freq_xlating_fir_filter_fcc(audio_decim,
                                               firdes.low_pass(2500.0,
                                                               baseband_rate,
                                                               2.4e3, 2e3,
                                                               firdes.WIN_HAMMING),
                                               57e3, baseband_rate)

        mpsk_receiver = digital.mpsk_receiver_cc(2, 0, cmath.pi/100.0, -0.06,
                                                 0.06, 0.5, 0.05,
                                                 baseband_rate/audio_decim/2375.0,
                                                 0.001, 0.005)

        complex_to_real = blocks.complex_to_real(1)
        binary_slicer = digital.binary_slicer_fb()
        keep_one_in_2 = blocks.keep_one_in_n(gr.sizeof_char*1, 2)
        diff_decoder = digital.diff_decoder_bb(2)

        self.connect(self.rtlsdr_source, fir0, self.wfm_rcv_rds, fir1,
                     cosine_filter, mpsk_receiver, complex_to_real,
                     binary_slicer, keep_one_in_2, diff_decoder,
                     self.rds_decoder)

        # RDS Decoder -> RDS Parser -> RDS Adapter
        self.msg_connect((self.rds_decoder, b'out'), (self.rds_parser, b'in'))
        self.msg_connect((self.rds_parser, b'out'), (self.rds_adapter, b'in'))

    @property
    def gain(self):
        """ The tuner's RF gain """
        return self._gain

    @gain.setter
    def gain(self, gain):
        """ Set the tuner's RF gain """
        # TODO: maybe prevent setting gain above known maximum of 49.6?
        self._gain = gain
        if gain == "auto":
            self.rtlsdr_source.set_gain_mode(True, 0)  # Turn auto-gain on
        elif isinstance(gain, Number):
            self.rtlsdr_source.set_gain_mode(False, 0)  # Turn auto-gain off
            self.rtlsdr_source.set_gain(self._gain, 0)
        else:
            raise ValueError("Expected either 'auto' or a number")

    @property
    def frequency(self):
        """ The tuner frequency (in Hertz) """
        return self._frequency

    @frequency.setter
    def frequency(self, frequency):
        """ Set the tuner frequency, in Hertz"""
        # TODO maybe check FM range and raise ValueError when trying to tune out of it
        self.rds_adapter.data.clear()
        self._frequency = frequency
        self.rtlsdr_source.set_center_freq(self._frequency, 0)

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, volume):
        if volume > 3:
            raise ValueError("volume range: 0 to 3, anything higher would cause clipping")
        self._volume = volume
        self.volume_multiplier.set_k((self._volume, ))


# TODO this should probably be an enum or something
rds_message_types = {0: "program_information",
                     1: "station_name",
                     2: "program_type",
                     3: "flags",
                     4: "radiotext",
                     5: "clock_time",
                     6: "alternative_frequencies"}
# TODO actually decode "flags" and "program_information"
# resources on that are: https://github.com/bastibl/gr-rds/blob/master/python/rdspanel.py
# and https://github.com/bastibl/gr-rds/blob/master/lib/parser_impl.cc


class RDSAdapter(gr.sync_block):
    """ RDSAdapter makes it easy to use RDS data """
    def __init__(self):
        gr.sync_block.__init__(self, name=b"rds_adapter", in_sig=None,
                               out_sig=None)
        self.message_port_register_in(pmt.intern(b'in'))
        self.set_msg_handler(pmt.intern(b'in'), self.msg_handler)
        self.callback = None
        """ a callback to call when new RDS data is recived"""
        self.data = {}  # TODO better interface for that than just a dict

    def msg_handler(self, msg):
        if pmt.is_tuple(msg):
            msg_type = pmt.to_long(pmt.tuple_ref(msg, 0))
            type_name = rds_message_types[msg_type]
            msg = pmt.symbol_to_string(pmt.tuple_ref(msg, 1))
            changed = False
            if type_name not in self.data:
                # We haven't seen this field before, add it to the cache
                if type_name == "alternative_frequencies":
                    # There can be more than one alternative freq, so split
                    self.data[type_name] = set(msg.split(', '))
                else:
                    self.data[type_name] = msg
                changed = True
            else:
                # we know this field, let's check if it changed
                # Alternative frequencies change often, so we keep all of them
                # (perhaps we should filter those with kHz because they're AM)
                if type_name == "alternative_frequencies":
                    for frequency in msg.split(', '):
                        if frequency not in self.data[type_name]:
                            # an alternative frequency we haven't seen yet
                            self.data[type_name].add(frequency)
                            changed = True
                elif self.data[type_name] != msg:
                    # The check is much simpler for any other field
                    changed = True
                    self.data[type_name] = msg

            if self.callback is not None and changed:
                # fire callback if we have new data
                self.callback(type_name, self.data[type_name])


def main():
    """ this main() is a demo. Don't actually use it """
    if rtlsdr.librtlsdr.rtlsdr_get_device_count() == 0:
        # This prevents us from supporting other types of SDRs, but
        # I don't know how to enumerate other SDRs, and a proper error
        # is better than just starting with null input
        raise Exception("No rtl-sdr devices found")
    rcv = MonoFMRceiver()

    def rds_callback_example(changed, data):
        """ Changed: the data field that changed. Data: the new value"""
        print("%s changed to %s" % (changed, data))
    rcv.rds_adapter.callback = rds_callback_example
    rcv.start()
    rcv.volume = 0.9  # example: setting volume to 90%
    # you can use rcv.frequency to set the frequency. remember it's in Hz.
    # You can also use rcv.rds_apater.data to read the RDS data cache
    try:
        raw_input('Press Enter to quit: ')
    except (EOFError, KeyboardInterrupt):
        pass
    rcv.stop()
    rcv.wait()


if __name__ == '__main__':
    main()
