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
__date__ = "2017-07-28"
# Created: 2017-07-07 19:16

from pprint import pformat
import datetime
import threading

from flotils import Loadable, StartStopable
from six import string_types, text_type
import telegram
import telegram.ext
from telegram.ext import Updater, MessageHandler, Filters


class TelegramClient(Loadable, StartStopable):
    
    def __init__(self, settings=None):
        if settings is None:
            settings = {}
        super(TelegramClient, self).__init__(settings)
        self._cache_path = self.join_path_prefix(settings.get('cache_path'))
        self._user_map = settings.get('user_map', {})
        """ Map telegram user ids to internal uuids
            :type : dict[int, string] | unicode """
        self._updater = Updater(
            token=settings['token']
        )
        self._poll_interval = settings.get("telegram_poll_interval", 0.0)
        self._timeout = settings.get("telegram_timeout", 10.0)
        self._block_unknown = settings.get("block_unknown_users", True)
        self._command_queue = []
        self._text_queue = []
        self._queue_lock = threading.RLock()
        self.new_text = threading.Event()
        """ New text in queue """
        self.new_command = threading.Event()
        """ New command in queue"""

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

    def cache_load(self):
        if not self._cache_path:
            return
        try:
            cache = self.load_settings(self._cache_path)
            if cache:
                self._command_queue = cache.get('commands', [])
                self._text_queue = cache.get('texts', [])
                self.info(
                    "Loaded {}|{} messages from cache".format(
                        len(self._command_queue), len(self._text_queue)
                    )
                )
        except:
            self.exception("Failed to load cache ({})".format(self._cache_path))

    def cache_save(self):
        if not self._cache_path:
            return
        try:
            self.save_settings(self._cache_path, {
                'commands': self._command_queue,
                'texts': self._text_queue
            })
            self.info(
                "Saved {}|{} messages to cache".format(
                    len(self._command_queue), len(self._text_queue)
                )
            )
        except:
            self.exception("Failed to save cache ({])".format(self._cache_path))

    def map_load(self):
        if isinstance(self._user_map, (string_types, text_type)):
            path = self.join_path_prefix(self._user_map)
            self.debug("Loading user mappings from {}".format(path))

            try:
                umap = self.load_settings(path)
                self.info(
                    "User mappings loaded ({} entries)".format(len(umap))
                )
                self._user_map = {int(k): v for k, v in umap.items()}
            except:
                self.exception("Failed to load user mappings from {}".format(
                    path
                ))

    def _error_handler(self, bot, update, error):
        try:
            self.error(update)
            raise error
        except telegram.error.Unauthorized:
            self.error("Remove update.message.chat_id from conversation list")
        except telegram.error.BadRequest:
            self.exception("Handle malformed requests")
        except telegram.error.TimedOut:
            self.error("Handle slow connection problems")
        except telegram.error.NetworkError:
            self.exception("Handle other connection problems")
        except telegram.error.ChatMigrated as e:
            self.error(
                "Chat_id of a group has changed, use e.new_chat_id instead"
            )
        except telegram.error.TelegramError:
            self.exception("Telegram exception occured")

    def _parse_message(self, update):
        """
        Parse update and return result

        :param update: Update to parse
        :type update: telegram.Update
        :return: Parsed result
        :rtype: dict
        """
        result = {}
        user = update.effective_user
        chat = update.effective_chat
        message = update.effective_message
        """ :type : telegram.Message """
        result['update_id'] = update.update_id

        if user:
            result['user'] = user.to_dict()
            result['mapped_user'] = self._user_map.get(user.id)
            if not result['mapped_user'] and self._block_unknown:
                self.warning("Blocked unknown user\n{}".format(result['user']))
                return None
        if chat:
            result['chat'] = chat.to_dict()
        if message:
            self.debug("Message: {}\n{}".format(message.date, message))
            result['message_id'] = message.message_id
            result['timestamp'] = message.date

            if result['timestamp']:
                # To utc
                result['timestamp'] = datetime.datetime.utcfromtimestamp(
                    message._totimestamp(result['timestamp'])
                )
            result['message'] = message.text

            # self.debug(message.parse_entities())
        return result

    def _text_handler(self, bot, update, user_data=None):
        """
        Handle incoming text messages

        :param bot: bot
        :type bot: telegram.Bot
        :param update: Message
        :type update: telegram.Update
        :param user_data: Custom user data
        :type user_data: dict
        :rtype: None
        """
        result = self._parse_message(update)

        if user_data:
            self.debug("User data: {}".format(pformat(user_data)))

        with self._queue_lock:
            if result:
                self._text_queue.append(result)
                self.new_text.set()
            else:
                self.warning("Did not add message\n{}".format(update))

    def _command_handler(self, bot, update, args=None):
        """
        Handle incoming command messages

        :param bot: bot
        :type bot: telegram.Bot
        :param update: Message
        :type update: telegram.Update
        :rtype: None
        """
        result = self._parse_message(update)

        if result.get('message') and result['message'].startswith("/"):
            parts = result['message'][1:].split()
            result['command'] = parts[0]
            result['args'] = " ".join(parts[1:])
        with self._queue_lock:
            if result:
                self._command_queue.append(result)
                self.new_command.set()
            else:
                self.warning("Did not add command\n{}".format(update))

    def get_commands(self):
        """
        Return all received commands

        :return: Rx commands
        :rtype: list[dict[unicode, object]]
        """
        with self._queue_lock:
            return self._command_queue[:]

    def delete_commands(self, ids):
        """
        Delete commands from queue

        :param ids: Which updates to delete
        :type ids: list[int]
        :rtype: None
        """
        if not ids:
            return
        with self._queue_lock:
            self._command_queue = [
                cmd
                for cmd in self._command_queue
                if cmd['update_id'] not in ids
            ]
            if len(self._command_queue) == 0:
                self.new_command.clear()

    def get_texts(self):
        """
        Return all received texts

        :return: Rx texts
        :rtype: list[dict[unicode, object]]
        """
        with self._queue_lock:
            return self._text_queue[:]

    def delete_texts(self, ids):
        """
        Delete texts from queue

        :param ids: Which updates to delete
        :type ids: list[int]
        :rtype: None
        """
        if not ids:
            return
        with self._queue_lock:
            self._text_queue = [
                cmd
                for cmd in self._text_queue
                if cmd['update_id'] not in ids
            ]
            if len(self._text_queue) == 0:
                self.new_text.clear()

    def send(self, to, text, reply_to_message_id=None, silent=False):
        self._updater.bot.send_message(
            to, text, reply_to_message_id=reply_to_message_id,
            disable_notification=silent
        )

    def reply(self, to, text, reply_to_message_id, silent=False):
        self._updater.bot.send_message(to, text, reply_to_message_id, silent)

    def start(self, blocking=False):
        self.debug("()")
        self.cache_load()
        if self._command_queue:
            # Got commands
            self.new_command.set()
        if self._text_queue:
            # Got commands
            self.new_text.set()
        self.map_load()
        self._updater.dispatcher.add_error_handler(self._error_handler)
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.command, self._command_handler
        ))
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.text, self._text_handler, pass_user_data=True
        ))

        self._updater.start_polling(
            poll_interval=self._poll_interval, timeout=self._timeout
        )
        super(TelegramClient, self).start(blocking)

    def stop(self):
        self.debug("()")
        super(TelegramClient, self).stop()
        self.cache_save()
        # Keeps hanging???
        self._updater.stop()
        self.cache_save()
