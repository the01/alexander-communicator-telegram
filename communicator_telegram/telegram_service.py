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
# Created: 2017-07-07 19:10

from pprint import pformat
import typing

from nameko.timer import timer
from nameko.extensions import DependencyProvider
import alexander_fw
from alexander_fw import CommunicatorService, StandaloneCommunicatorService
from alexander_fw.dto import InputMessage
from flotils import get_logger

from .__version__ import __version__ as module_version
from .telegram import TelegramClient


logger = get_logger()


class TelegramDependency(DependencyProvider):

    def setup(self):
        settings = self.container.config['telegram']
        self.instance = TelegramClient(settings)
        super(TelegramDependency, self).setup()

    def start(self):
        logger.debug("Telegram client starting..")
        self.instance.start(False)
        logger.info("Telegram client started")
        super(TelegramDependency, self).start()

    def stop(self):
        try:
            self.instance.stop()
        except:
            logger.exception("Failed to close instance")
        super(TelegramDependency, self).stop()

    def get_dependency(self, worker_ctx):
        return self.instance


class StandaloneTelegramService(StandaloneCommunicatorService):
    name = "service_communicator_telegram"
    allowed = ["status", "version", "say", "send", "send_user"]
    telegram: TelegramClient = None

    def version(self) -> str:
        return module_version

    def send(
            self,
            to: typing.Union[str, int],
            text: typing.Union[str, typing.List[typing.Union[str, dict]], dict],
            reply_to_message_id=None, silent: bool = False,
    ) -> None:
        # TODO: Send by user name
        return self.telegram.send(to, text, reply_to_message_id, silent)

    def get_user(
            self, meta: typing.Optional[typing.Dict[str, typing.Any]],
    ) -> typing.Optional[typing.Any]:
        if not meta:
            return None
        if meta.get('user') and meta['user'].get('id'):
            return meta['user']['id']
        return None

    def send_user(
            self, user_uuid: str,
            text: typing.Union[str, typing.List[typing.Union[str, dict]], dict],
            reply_to_message_id=None, silent: bool = False,
    ) -> None:
        eid = self.get_user({'mapped_user': user_uuid})
        return self.send(eid, text, reply_to_message_id, silent)

    def do_say(self, msg: alexander_fw.dto.actor_msg.ActorMessage) -> None:
        logger.info("Got:\n{}".format(pformat(msg.to_dict())))
        meta: dict = msg.metadata
        to = reply = text = None

        if meta:
            if meta.get('message_id'):
                reply = meta['message_id']
            if meta.get('chat'):
                to = meta['chat']['id']
            if to is None:
                to = self.get_user(meta)
            text = msg.result
        if text and to:
            self.telegram.send(to, text, reply)

    def pop_commands(self) -> typing.List[typing.Dict[str, typing.Any]]:
        msgs = self.telegram.get_commands()
        self.telegram.delete_commands([msg['update_id'] for msg in msgs])
        return msgs

    def pop_texts(self) -> typing.List[typing.Dict[str, typing.Any]]:
        msgs = self.telegram.get_texts()
        self.telegram.delete_texts([msg['update_id'] for msg in msgs])
        return msgs

    def to_input_message(
            self, t_msg: typing.Dict[str, typing.Any]
    ) -> alexander_fw.dto.InputMessage:
        result = InputMessage()
        t = t_msg.get('timestamp')
        if t:
            result.timestamp = t
        if t_msg.get('message'):
            # Should only send message of this type?
            result.data = t_msg['message']
        result.metadata = t_msg
        return result


class TelegramService(CommunicatorService, StandaloneTelegramService):

    telegram: TelegramClient = TelegramDependency()

    @timer(interval=1)
    def _timer_msgs_emit(self):
        cmds = self.pop_commands()

        for cmd in cmds:
            im = None

            try:
                im = self.to_input_message(cmd)
                self.communicate(im)
            except:
                logger.exception("Failed to communicate message\n{}".format(im))

        txts = self.pop_texts()

        for txt in txts:
            im = None

            try:
                im = self.to_input_message(txt)
                self.communicate(im)
            except:
                logger.exception("Failed to communicate message\n{}".format(im))
