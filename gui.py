#!/usr/bin/env python2
# -*- coding: utf-8 -*-

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

from tuner import MonoFMRceiver
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class SignalHandler(object):
    def __init__(self, receiver, builder):
        self.receiver = receiver
        self.builder = builder

    def window_closed(self, *args):
        self.receiver.stop()
        self.receiver.wait()
        Gtk.main_quit(*args)

    def window_shown(self, window):
        # TODO: this shows clearly the RDS api needs to be better
        header = self.builder.get_object("headerbar")
        try:
            radiotext = self.receiver.rds_adapter.data["radiotext"]
        except KeyError:
            radiotext = ""
        if radiotext != "":
            header.set_subtitle(radiotext)
        else:
            header.set_subtitle("%s MHz" % (self.receiver.frequency / 1e6))
        try:
            station_name = self.receiver.rds_adapter.data["station_name"]
        except KeyError:
            station_name = ""
        if station_name != "":
            header.set_title(station_name)
        else:
            header.set_title("%s FM" % (self.receiver.frequency / 1e6))

    def volume_changed(self, volume):
        print("New volume: %s" % volume.get_value())
        self.receiver.volume = volume.get_value()

    def frequency_changed(self, freq):
        print("New freq: %s" % freq.get_value())
        header = self.builder.get_object("headerbar")
        header.set_title("%s FM" % freq.get_value())
        header.set_subtitle("%s MHZ" % freq.get_value())
        self.receiver.frequency = freq.get_value() * 1e6


def main():
    receiver = MonoFMRceiver()
    builder = Gtk.Builder()
    builder.add_from_file("tuner.ui")
    builder.connect_signals(SignalHandler(receiver, builder))
    header = builder.get_object("headerbar")
    window = builder.get_object("main_window")
    window.show_all()

    def rds_callback(changed, data):
        if changed == "station_name":
            header.set_title(data)
        elif changed == "radiotext":
            header.set_subtitle(data)
        elif changed == "program_type":
            program_type = builder.get_object("program_type")
            if data == "None":
                data = ""
            program_type.set_text(data)
    receiver.rds_adapter.callback = rds_callback
    receiver.start()

    Gtk.main()

if __name__ == "__main__":
    main()
