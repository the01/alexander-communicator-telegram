# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

__author__ = "d01"
__email__ = "jungflor@gmail.com"
__copyright__ = "Copyright (C) 2017, Florian JUNG"
__license__ = "MIT"
__version__ = "0.1.1"
__date__ = "2017-12-02"
# Created: 2017-07-08 13:34

import threading
import errno

from flotils.runable import SignalStopWrapper
from flotils import StartStopable, Loadable
from alexander_fw import setup_kombu, RPCListener
from alexander_fw.service import event_dispatcher

from communicator_telegram import StandaloneTelegramService, TelegramClient


class TelegramRunner(Loadable, StartStopable, SignalStopWrapper):

    def __init__(self, settings=None):
        if settings is None:
            settings = {}
        super(TelegramRunner, self).__init__(settings)

        nameko_settings = settings['nameko']
        """ :type : dict """
        telegram_settings = settings['telegram']
        """ :type : dict """

        if self._prePath is not None:
            telegram_settings.setdefault('path_prefix', self._prePath)
        if self._prePath is not None:
            nameko_settings.setdefault('path_prefix', self._prePath)
        self.dispatcher = event_dispatcher(nameko_settings)
        self.telegram = TelegramClient(telegram_settings)
        self.service = StandaloneTelegramService()
        self.service.dispatch_intent = self._dispatch_intent
        self.service.telegram = self.telegram
        nameko_settings['service_name'] = self.service.name
        nameko_settings['service'] = self.service
        nameko_settings['allowed_functions'] = self.service.allowed
        self.listener = RPCListener(nameko_settings)
        self._done = threading.Event()
        self._polling_timeout = settings.get('polling_interval', 2.0)

    def _thread_wrapper(self, function, *args, **kwargs):
        """
        Wrap function for exception handling with threaded calls

        :param function: Function to call
        :type function: callable
        :rtype: None
        """
        try:
            function(*args, **kwargs)
        except:
            self.exception("Threaded execution failed")

    def _run_message_watcher(self):
        timeout = self._polling_timeout
        while not self._done.wait(timeout):
            if self.telegram.new_command.is_set():
                cmds = self.service.pop_commands()
                for cmd in cmds:
                    try:
                        im = self.service.to_input_message(cmd)
                        self.service.communicate(im)
                    except:
                        self.exception(
                            "Failed to communicate message\n{}".format(im)
                        )
            if self.telegram.new_text.is_set():
                txts = self.service.pop_texts()
                for txt in txts:
                    try:
                        im = self.service.to_input_message(txt)
                        self.service.communicate(im)
                    except:
                        self.exception(
                            "Failed to communicate message\n{}".format(im)
                        )
            if self.telegram.new_text.is_set() \
                    or self.telegram.new_command.is_set():
                # Got more messages -> don't sleep
                timeout = 0.0
            else:
                timeout = self._polling_timeout

    def _dispatch_intent(self, event_type, event_data):
        self.dispatcher("manager_intent", event_type, event_data)

    def start(self, blocking=False):
        self.debug("()")
        super(TelegramRunner, self).start(False)
        self.debug("Starting telegram client..")
        try:
            self.telegram.start(False)
        except:
            self.exception("Failed to start telegram client")
            self.stop()
            return
        self.info("Telegram client running")
        setup_kombu()
        self.debug("Starting rpc listener")
        try:
            self.listener.start(False)
        except:
            self.exception("Failed to start rpc listener")
            self.stop()
            return
        self.info("RPC listener running")
        self._done.clear()
        if blocking:
            try:
                self._run_message_watcher()
            except IOError as e:
                if e.errno == errno.EINTR:
                    self.warning("Interrupted function in message loop")
                else:
                    self.exception("Failed to run message loop")
            except:
                self.exception("Failed to run message loop")
                self.stop()
                return
        else:
            try:
                a_thread = threading.Thread(
                    target=self._thread_wrapper,
                    args=(self._run_message_watcher,)
                )
                a_thread.daemon = True
                a_thread.start()
            except:
                self.exception("Failed to run message loop")
                self.stop()
                return

    def stop(self):
        self.debug("()")
        self._done.set()
        super(TelegramRunner, self).stop()
        self.debug("Stopping rpc listener")
        try:
            self.listener.stop()
        except:
            self.exception("Failed to stop rpc listener")
        else:
            self.info("RPC listener stopped")
        self.debug("Stopping telegram client")
        try:
            self.telegram.stop()
        except:
            self.exception("Failed to stop telegram client")
        else:
            self.info("Telegram client stopped stopped")


if __name__ == "__main__":
    import logging
    import logging.config
    import os
    import sys
    import argparse

    from flotils.logable import default_logging_config, get_logger
    from flotils.loadable import load_file

    logging.captureWarnings(True)
    logging.config.dictConfig(default_logging_config)
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger("amqp").setLevel(logging.INFO)
    logging.getLogger("kombu").setLevel(logging.DEBUG)
    logging.getLogger("nameko").setLevel(logging.DEBUG)
    logging.getLogger("__main__").setLevel(logging.DEBUG)

    argparser = argparse.ArgumentParser(prog="run_standalone")
    argparser.add_argument("--debug", action="store_true")
    argparser.add_argument("--log-file", type=str, default=None)
    argparser.add_argument("--log-console", action="store_true")
    argparser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    argparser.add_argument(
        "-s", "--settings", type=str
    )

    args = argparser.parse_args()
    settings_file = args.settings
    sett = {}

    if settings_file:
        sett.update(load_file(settings_file))
        if "logger" in sett:
            logger_sett = dict(default_logging_config)
            logger_sett.update(sett['logger'])
            if "settings_file" in logger_sett:
                logger_sett.update(load_file(logger_sett['settings_file']))
                del logger_sett['settings_file']
            handlers = logger_sett['handlers']
            root_logger = logger_sett['loggers']['']
            root_logger.setdefault('handlers', [])
            if args.log_file:
                handlers['file']['filename'] = args.log_file
                root_logger['handlers'].append("file")
            else:
                del handlers['file']
            if args.log_console or not args.log_console:
                root_logger['handlers'].append("console")
            logging.config.dictConfig(logger_sett)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger = get_logger()
    pid = os.getpid()
    logger.info("Detected pid {}".format(pid))
    logger.info("Using virtualenv {}".format(hasattr(sys, 'real_prefix')))
    logger.info("Using supervisor {}".format(
        bool(os.getenv('SUPERVISOR_ENABLED', False)))
    )

    settings_file = args.settings
    sett = {}
    if settings_file:
        sett['settings_file'] = settings_file

    instance = TelegramRunner(sett)

    try:
        instance.start(True)
    except KeyboardInterrupt:
        pass
    except:
        logger.exception("Failed to run telegram")
    finally:
        instance.stop()
