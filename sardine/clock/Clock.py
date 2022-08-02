import asyncio
import inspect
import itertools
import mido
from rich import print
import time
from typing import Callable, Coroutine, Union

from .AsyncRunner import AsyncRunner
from ..io.MidiIo import MIDIIo

# Aliases
atask = asyncio.create_task
sleep = asyncio.sleep
CoroFunc = Callable[..., Coroutine]


class Clock:

    """
    Naive MIDI Clock and scheduler implementation. This class
    is the core of Sardine. It generates an asynchronous MIDI
    clock and will schedule functions on it accordingly.

    Keyword arguments:
    port_name: str -- Exact String for the MIDIOut Port.
    bpm: Union[int, float] -- Clock Tempo in beats per minute
    beats_per_bar: int -- Number of beats in a given bar
    """

    def __init__(self, bpm: Union[float, int] = 120, beat_per_bar: int = 4):

        self._midi = MIDIIo()
        # Clock maintenance related
        self.runners: dict[str, AsyncRunner] = {}
        self.running = False
        self._debug = False
        # Timing related
        self._bpm = bpm
        self.initial_time = 0
        self.delta = 0
        self.beat = -1
        self.ppqn = 48
        self._phase_gen = itertools.cycle(range(1, self.ppqn + 1))
        self.phase = 0
        self.beat_per_bar = beat_per_bar
        self._current_beat_gen = itertools.cycle(
                range(1, self.beat_per_bar + 1))
        self.current_beat = 0
        self.elapsed_bars = 0
        self.tick_duration = self._get_tick_duration()
        self.tick_time = 0

    def init_reset(self,
            runners: dict[str, AsyncRunner],
            bpm: Union[float, int],
            midi: MIDIIo,
            beat_per_bar: int):
        self._midi = midi
        self.runners: dict[str, AsyncRunner] = {}
        self._debug = False
        self._bpm = bpm
        self.initial_time = 0
        self.delta = 0
        # self.beat = -1
        self.ppqn = 48
        self._phase_gen = itertools.cycle(range(1, self.ppqn + 1))
        self.phase = 0
        self.beat_per_bar = beat_per_bar
        self._current_beat_gen = itertools.cycle(
                range(1, self.beat_per_bar + 1))
        self.current_beat = 0
        self.elapsed_bars = 0
        self.tick_duration = self._get_tick_duration()
        self.tick_time = 0

    # ---------------------------------------------------------------------- #
    # Setters and getters

    def get_bpm(self):
        """ BPM Getter """
        return self._bpm

    def set_bpm(self, new_bpm: int) -> None:
        """ BPM Setter """
        if 1 < new_bpm < 800:
            self._bpm = new_bpm
            self.tick_duration = self._get_tick_duration()

    def get_debug(self):
        """ Debug getter """
        return self._debug

    def set_debug(self, boolean: bool):
        """ Debug setter """
        self._debug = boolean

    bpm = property(get_bpm, set_bpm)
    debug = property(get_debug, set_debug)

    # ---------------------------------------------------------------------- #
    # Private methods

    def _get_tick_duration(self):
        return ((60 / self.bpm) / self.ppqn) - self.delta

    def _update_phase(self) -> None:
        """ Update the current phase in MIDI Clock """
        self.phase = next(self._phase_gen)

    def _update_current_beat(self) -> None:
        """ Update the current beat in bar """
        self.current_beat = next(self._current_beat_gen)

    # ---------------------------------------------------------------------- #
    # Scheduler methods

    def schedule(self, func: CoroFunc, /, *args, **kwargs):
        """Schedules the given function to be executed."""
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f'func must be a coroutine function, not {type(func).__name__}')
        elif not self.running:
            raise RuntimeError(f"Clock must be started before functions can be scheduled")

        name = func.__name__
        runner = self.runners.get(name)
        if runner is None:
            runner = self.runners[name] = AsyncRunner(self)

        runner.push(func, *args, **kwargs)
        if not runner.started():
            runner.start()

    # ---------------------------------------------------------------------- #
    # Public methods

    def remove(self, func: CoroFunc, /):
        """Schedules the given function to stop execution."""
        runner = self.runners[func.__name__]
        runner.stop()

    def get_phase(self):
        return self.phase

    def print_children(self):
        """ Print all children on clock """
        [print(child) for child in self.runners]

    def ticks_to_next_bar(self) -> None:
        """ How many ticks until next bar? """
        return (self.ppqn - self.phase - 1) * self._get_tick_duration()

    async def play_note(self, note: int = 60, channel: int = 0,
                        velocity: int = 127,
                        duration: Union[float, int] = 1) -> None:

        """
        OBSOLETE // Was used to test things but should be removed.
        Dumb method that will play a note for a given duration.

        Keyword arguments:
        note: int -- the MIDI note to be played (default 1.0)
        duration: Union [int, float] -- MIDI tick time multiplier (default 1.0)
        channel: int -- MIDI Channel (default 0)
        velocity: int -- MIDI velocity (default 127)
        """

        async def send_something(message):
            """ inner non blocking function """
            asyncio.create_task(self._midi.send_async(message))

        note_on = mido.Message('note_on', note=note, channel=channel, velocity=velocity)
        note_off = mido.Message('note_off', note=note, channel=channel, velocity=velocity)
        await send_something(note_on)
        await asyncio.sleep(self.tick_duration * duration)
        await send_something(note_off)

    async def run_clock_initial(self):
        """ The MIDIClock needs to start """
        self.run_clock()

    def start(self):
        """ Restart message """
        # Switching runners on (will bug)
        for runner in self.runners.values():
            runner._stop = False
            self.remove(runner)
        if not self.running:
            asyncio.create_task(self._send_start(initial=True))

    def reset(self) -> None:
        self.init_reset(
                runners=self.runners,
                bpm=self._bpm,
                midi=self._midi,
                beat_per_bar=self.beat_per_bar)

    def stop(self) -> None:
        """
        MIDI Stop message.
        """
        # Kill every runner
        for runner in self.runners.values():
            runner._stop = True

        self.running = False
        self._midi.send_stop()
        self._midi.send(mido.Message('stop'))
        self.init_reset(
                runners=self.runners,
                bpm=self._bpm,
                midi=self._midi,
                beat_per_bar=self.beat_per_bar)

    async def _send_start(self, initial: bool = False) -> None:
        """ MIDI Start message """
        self._midi.send(mido.Message('start'))
        self.running = True
        if initial:
            asyncio.create_task(self.run_clock())

    def next_beat_absolute(self):
        """ Return time between now and next beat in absolute time """
        return self.tick_duration * (self.ppqn - self.phase)

    def log(self) -> None:

        """
        Pretty print information about Clock timing on the console.
        Used for debugging purposes. Not to be used when playing,
        can be very verbose. Will overflow the console in no-time.
        """

        color = "[bold red]" if self.phase == 1 else "[bold yellow]"
        first = color + f"BPM: {self.bpm}, PHASE: {self.phase:02}, DELTA: {self.delta:2f}"
        second = color + f" || [{self.tick_time}] {self.current_beat}/{self.beat_per_bar}"
        print(first + second)


    async def run_clock(self):

        """
        Main Method for the MIDI Clock. Full of errors and things that
        msut be fixed. Drift can happen, and it might need a full rewrite.

        Keyword arguments:
        debug: bool -- print debug messages on stdout.
        """

        async def _clock_update():
            """ Things the clock should do every tick """

            self.tick_duration = self._get_tick_duration()

            begin = time.perf_counter()
            self.delta = 0

            await asyncio.sleep(self.tick_duration)

            # test to get right tempo
            if self.phase % 2 == 0:
                asyncio.create_task(self._midi.send_clock_async())

            # Time grains
            self.tick_time += 1
            self._update_phase()

            # XPPQN = 1 Beat
            if self.phase == 1:
                self._update_current_beat()
            if self.phase == 1 and self.current_beat == 1:
                self.elapsed_bars += 1

            # End of it
            end = time.perf_counter()
            self.delta = end - begin - self.tick_duration
            if self._debug:
                self.log()

        while self.running:
            await _clock_update()

    def get_tick_time(self):
        """ Indirection to get tick time """
        return self.tick_time

    def ramp(self, min: int, max: int):
        """ Generate a ramp between min and max using phase """
        return self.phase % (max - min + 1) + min

    def iramp(self, min: int, max: int):
        """ Generate an inverted ramp between min and max using phase"""
        return self.ppqn - self.phase % (max - min + 1) + min
