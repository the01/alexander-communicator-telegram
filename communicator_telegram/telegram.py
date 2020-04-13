# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

__author__ = "d01"
__email__ = "jungflor@gmail.com"
__copyright__ = "Copyright (C) 2017-20, Florian JUNG"
__license__ = "MIT"
__version__ = "0.2.0"
__date__ = "2020-04-13"
# Created: 2017-07-07 19:16

from pprint import pformat
import datetime
import threading
import time
import typing
import base64
from io import BytesIO

from flotils import Loadable, StartStopable
from six import string_types, text_type
import telegram
import telegram.ext
from telegram.ext import Updater, MessageHandler, Filters
from telegram.error import TimedOut


class TelegramClient(Loadable, StartStopable):
    
    def __init__(
            self, settings: typing.Optional[typing.Dict[str, typing.Any]] = None
    ) -> None:
        if settings is None:
            settings = {}
        super().__init__(settings)

        self._cache_path: typing.Optional[str] = self.join_path_prefix(
            settings.get('cache_path')
        )
        self._user_map: typing.Union[typing.Dict[int, str], str] = \
            settings.get('user_map', {})
        """ Map telegram user ids to internal uuids """
        self._user_whitelist: typing.Optional[str, typing.List[int]] = \
            settings.get('user_whitelist', None)
        """ TODO """

        self._updater = Updater(
            token=settings['token'], use_context=True,
        )
        self._poll_interval: float = settings.get("telegram_poll_interval", 0.0)
        self._timeout: float = settings.get("telegram_timeout", 10.0)
        self._block_unknown: bool = settings.get("block_unknown_users", True)
        self._max_resends: int = settings.get("max_retry_send", 2)
        self._command_queue: typing.List = []
        self._text_queue: typing.List = []
        self._queue_lock = threading.RLock()
        self.new_text = threading.Event()
        """ New text in queue """
        self.new_command = threading.Event()
        """ New command in queue """

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
        except Exception:
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
        except Exception:
            self.exception("Failed to save cache ({])".format(self._cache_path))

    def map_load(self):
        if isinstance(self._user_map, (string_types, text_type)):
            path = self.join_path_prefix(self._user_map)
            self.debug("Loading user mappings from {}".format(path))

            try:
                umap = self.load_file(path)
                self.info(
                    "User mappings loaded ({} entries)".format(len(umap))
                )
                self._user_map = {int(k): v for k, v in umap.items()}
            except:
                self.exception("Failed to load user mappings from {}".format(
                    path
                ))

    def whitelist_load(self):
        if not self._user_whitelist:
            return
        if isinstance(self._user_whitelist, (string_types, text_type)):
            path = self.join_path_prefix(self._user_whitelist)
            self.debug("Loading user whitelist from {}".format(path))

            try:
                ulist = self.load_file(path)
                self.info(
                    "User whitelist loaded ({} entries)".format(len(ulist))
                )
                self._user_whitelist = [int(v) for v in ulist]
            except:
                self.exception("Failed to load user whitelist from {}".format(
                    path
                ))

    def get_user_external(self, user_id):
        return None

    def get_user(self, user_id):
        try:
            user = self.get_user_external(user_id)
        except Exception:
            self.exception("Failed to get user from external")
            user = None
        if not user:
            user = self._user_map.get(user_id)
        return user

    # def _error_handler(self, bot, update, error):
    def _error_handler(
            self, update: telegram.Update, context: telegram.ext.CallbackContext
    ) -> None:
        try:
            if update:
                self.error(update)
            raise context.error
        except telegram.error.Unauthorized:
            self.error("Remove update.message.chat_id from conversation list")
        except telegram.error.BadRequest:
            self.exception("Handle malformed requests")
        except telegram.error.TimedOut:
            self.warning("Handle slow connection problems")
        except telegram.error.NetworkError:
            self.exception("Handle other connection problems")
        except telegram.error.ChatMigrated:
            self.error(
                "Chat_id of a group has changed, use e.new_chat_id instead"
            )
        except telegram.error.TelegramError:
            self.exception("Telegram exception occurred")

    def _parse_message(
            self, update: telegram.Update, bot: telegram.Bot
    ) -> typing.Optional[typing.Dict[str, typing.Any]]:
        """
        Parse update and return result

        :param update: Update to parse
        :param bot:
        :return: Parsed result
        """
        result = {}
        user = update.effective_user
        chat = update.effective_chat
        message: telegram.Message = update.effective_message
        result['update_id'] = update.update_id

        if user:
            result['user'] = user.to_dict()
            if isinstance(self._user_whitelist, list):
                # If whitelist is list -> user must be inside
                if not user.id in self._user_whitelist:
                    self.warning("Blocked not whitelisted user\n{}".format(
                        result['user']
                    ))
                    return None

            result['mapped_user'] = self.get_user(user.id)
            if not result['mapped_user'] and self._block_unknown:
                self.warning("Blocked unknown user\n{}".format(result['user']))
                return None
        else:
            self.error("No user - blocking")
            return None

        if chat:
            result['chat'] = chat.to_dict()
        if message:
            self.debug("Message: {}\n{}".format(message.date, message))
            result['message_id'] = message.message_id
            result['timestamp'] = message.date

            if result['timestamp']:
                dt_obj = message.date
                # To utc
                try:
                    # Python 3.3+
                    result['timestamp'] = int(dt_obj.timestamp())
                except AttributeError:
                    # Python 3 (< 3.3) and Python 2
                    result['timestamp'] = int(time.mktime(dt_obj.timetuple()))
                result['timestamp'] = datetime.datetime.utcfromtimestamp(
                    result['timestamp']
                )
            result['message'] = message.text
            if message.location:
                result['location'] = message.location.to_dict()
            if message.photo:
                psize = message.photo[-1]
                file = bot.get_file(psize.file_id)
                self.debug(file)
                bio = BytesIO()
                bio.name = "image"
                file.download(out=bio)
                bio.flush()
                bio.seek(0)
                result['photo'] = base64.b64encode(bio.read())
                bio.close()

            # self.debug(message.parse_entities())
        return result

    def _text_handler(
            self, update: telegram.Update, context: telegram.ext.CallbackContext
    ):
        result = self._parse_message(update, context.bot)

        if result is None:
            # Blocked user
            return

        user_data = context.user_data

        if user_data:
            self.debug("User data: {}".format(pformat(user_data)))

        with self._queue_lock:
            if result:
                self._text_queue.append(result)
                self.new_text.set()
            else:
                self.warning("Did not add message\n{}".format(update))

    def _command_handler(
            self, update: telegram.Update, context: telegram.ext.CallbackContext
    ) -> None:
        """
        Handle incoming command messages

        :param bot: bot
        :param update: Message
        """
        result = self._parse_message(update, context.bot)

        if result is None:
            # Blocked user
            return

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

    def get_commands(self) -> typing.List[typing.Dict[str, typing.Any]]:
        """
        Return all received commands

        :return: Rx commands
        """
        with self._queue_lock:
            return self._command_queue[:]

    def delete_commands(self, ids: typing.List[int]) -> None:
        """
        Delete commands from queue

        :param ids: Which updates to delete
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

    def get_texts(self) -> typing.List[typing.Dict[str, typing.Any]]:
        """
        Return all received texts

        :return: Rx texts
        """
        with self._queue_lock:
            return self._text_queue[:]

    def delete_texts(self, ids: typing.List[int]) -> None:
        """
        Delete texts from queue

        :param ids: Which updates to delete
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

    def send(
            self,
            to: typing.Union[str, int],
            text: typing.Union[str, typing.List[typing.Union[str, dict]], dict],
            reply_to_message_id=None, silent: bool = False, tries: int = 0,
    ) -> None:
        inp_list = text

        if not isinstance(inp_list, list):
            inp_list = [inp_list]

        for i, text in enumerate(inp_list):
            if isinstance(text, dict) and text.get('type') == "image":
                try:
                    decoded = base64.b64decode(text.get('value'))
                    bio = BytesIO()
                    bio.name = "image"
                    bio.write(decoded)
                    bio.flush()
                    bio.seek(0)
                except Exception:
                    self.debug("Not b64")
                else:
                    try:
                        self._updater.bot.send_photo(
                            to, bio, reply_to_message_id=reply_to_message_id
                        )
                        continue
                    except TimedOut:
                        if tries >= self._max_resends:
                            raise
                        self.warning("Send timed out, retrying #{}..".format(tries))
                        return self.send(
                            to, inp_list[i:], reply_to_message_id, silent, tries + 1
                        )
            if text:
                text = "{}".format(text)

            try:
                self._updater.bot.send_message(
                    to, text, reply_to_message_id=reply_to_message_id,
                    disable_notification=silent
                )
            except TimedOut:
                if tries >= self._max_resends:
                    raise
                self.warning("Send timed out, retrying #{}..".format(tries))
                return self.send(
                    to, inp_list[i:], reply_to_message_id, silent, tries + 1
                )

    def reply(
            self,
            to: typing.Union[str, int],
            text: typing.Union[str, typing.List[typing.Union[str, dict]], dict],
            reply_to_message_id, silent: bool = False
    ):
        self.send(to, text, reply_to_message_id, silent)

    def start(self, blocking: bool = False):
        self.debug("()")
        self.cache_load()

        if self._command_queue:
            # Got commands
            self.new_command.set()
        if self._text_queue:
            # Got commands
            self.new_text.set()

        self.map_load()
        self.whitelist_load()

        # Setup telegram callbacks
        self._updater.dispatcher.add_error_handler(self._error_handler)
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.command, self._command_handler
        ))
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.text, self._text_handler,
        ))
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.location, self._text_handler,
        ))
        self._updater.dispatcher.add_handler(MessageHandler(
            Filters.photo, self._text_handler,
        ))

        self._updater.start_polling(
            poll_interval=self._poll_interval, timeout=self._timeout
        )
        super(TelegramClient, self).start(blocking)

    def stop(self):
        self.debug("()")
        super().stop()
        self.cache_save()
        # Keeps hanging???
        try:
            self._updater.stop()
        except Exception:
            self.exception("Failed to stop updater")
        self.cache_save()
