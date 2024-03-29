# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

__author__ = "d01"
__email__ = "jungflor@gmail.com"
__copyright__ = "Copyright (C) 2017-20, Florian JUNG"
__license__ = "MIT"
__version__ = "0.1.2"
__date__ = "2020-04-14"
# Created: 2017-07-08 13:34

import errno
import threading
import time

from flotils.runable import SignalStopWrapper
from flotils import StartStopable, Loadable
from nameko.standalone.rpc import ClusterRpcProxy
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
        self.telegram.get_user_external = self._rpc_service_user_get_authorized
        self.service = StandaloneTelegramService()
        self.service.dispatch_intent = self._dispatch_intent
        self.service.telegram = self.telegram
        self._service_get_user = self.service.get_user
        self.service.get_user = self._rpc_service_user_external_id
        nameko_settings['service_name'] = self.service.name
        nameko_settings['service'] = self.service
        nameko_settings['allowed_functions'] = self.service.allowed
        self.listener = RPCListener(nameko_settings)
        self._cluster_proxy = ClusterRpcProxy(
            nameko_settings, timeout=nameko_settings.get('rpc_timeout', None)
        )
        self._proxy = None
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

    def _rpc_service_user_get_authorized(self, user_id):
        self.debug("({})".format(user_id))
        if not self._proxy:
            self.warning("No proxy available")
            return None
        resp = self._proxy.service_user.get_authorized(
            self.service.name, user_id
        )
        if not resp:
            return None
        user, permission = resp
        # TODO: check permission
        if not permission or not user:
            return None
        self.debug("Matched user: {}".format(user))
        return user.get('uuid')

    def _rpc_service_user_external_id(self, meta):
        self.debug("({})".format(meta))
        eid = self._service_get_user(meta)
        if eid is not None:
            return eid
        if not meta or not meta.get('mapped_user'):
            return None
        if not self._proxy:
            self.warning("No proxy available")
            return None

        resp = self._proxy.service_user.external_id(
            meta['mapped_user'], self.service.name
        )
        if not resp:
            return None
        return resp

    def start(self, blocking=False):
        self.debug("()")
        super(TelegramRunner, self).start(False)
        self.debug("Starting rpc proxy..")
        tries = 3
        sleep_time = 1.4

        while tries > 0:
            self.debug("Trying to establish nameko proxy..")
            try:
                self._proxy = self._cluster_proxy.start()
            except Exception:
                if tries <= 1:
                    raise
                self.exception("Failed to connect proxy")
                self.info("Sleeping {}s".format(round(sleep_time, 2)))
                time.sleep(sleep_time)
                sleep_time **= 2
            else:
                break
            tries -= 1

        self.service.proxy = self._proxy
        self.debug("Starting telegram client..")

        try:
            self.telegram.start(False)
        except Exception:
            self.exception("Failed to start telegram client")
            self.stop()
            return

        self.info("Telegram client running")
        self.debug("Starting rpc listener..")

        try:
            self.listener.start(False)
        except Exception:
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
            except Exception:
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
            except Exception:
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
            self.info("Telegram client stopped")
        self.debug("Stopping cluster proxy..")
        try:
            self._cluster_proxy.stop()
        except:
            self.exception("Failed to stop cluster proxy")
        else:
            self.info("RPC proxy stopped")
        finally:
            self._proxy = None


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
    logger.info("Detected pid {}".format(os.getpid()))
    logger.info("Using virtualenv {}".format(hasattr(sys, 'real_prefix')))
    logger.info("Using supervisor {}".format(
        bool(os.getenv('SUPERVISOR_ENABLED', False)))
    )

    setup_kombu()
    instance = TelegramRunner(sett)

    try:
        instance.start(True)
    except KeyboardInterrupt:
        pass
    except:
        logger.exception("Failed to run telegram")
    finally:
        instance.stop()
