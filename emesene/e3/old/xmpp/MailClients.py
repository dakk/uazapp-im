# -*- coding: utf-8 -*-

#    This file is part of emesene.
#
#    emesene is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    emesene is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with emesene; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#
#    Module written by Andrea Stagi <stagi.andrea(at)gmail.com>
#

import imaplib
from threading import Thread, Timer

class MailMessage(object):

    def __init__(self, name, address, subject, post_url, form_data):
        self._name = name
        self._subject = subject
        self._address = address
        self._post_url = post_url
        self._form_data = form_data

    @property
    def name(self):
        """The name of the person who sent the email"""
        return self._name

    @property
    def address(self):
        """Email address of the person who sent the email"""
        return self._address

    @property
    def subject(self):
        """Email subject"""
        return self._subject

    @property
    def post_url(self):
        """post url"""
        return self._post_url

    @property
    def form_data(self):
        """form url"""
        return self._form_data

class MailClient(Thread):

    def __init__(self, session, server = "", port = 0, username = "", password = ""):
        Thread.__init__(self)
        self.setDaemon(True)
        self._username = username
        self._password = password
        self._server = server
        self._port = port
        self._handlers = {}
        self._onrun = True
        self._session = session
        self.timer = None

    def register_handler(self, name, callback):
        self._handlers[name] = callback

    def on_run(self):
        if self._onrun:
            self.timer = Timer(5.0, self.on_run)
            self.timer.start()

    def on_initialize(self):
        pass

    def on_end(self):
        pass

    def run(self):
        self.on_initialize()
        self.on_run()

    def update_counter(self):
        pass

    def new_mails(self):
        pass

    def stop(self):
        self._onrun = False
        if self.timer:
            self.timer.cancel()
        self.on_end()


class NullMail(MailClient):

    def __init__(self):
        MailClient.__init__(self, None)

    def run(self):
        pass


class IMAPMail(MailClient):

    def __init__(self, session, server, port, username, password):
        MailClient.__init__(self, session, server, port, username, password)
        self._imap_server = imaplib.IMAP4_SSL(self._server, self._port)
        self._count = 0

    def on_run(self):
        old_count, new_count = self.update_counter()
        if old_count < new_count:
            self._handlers["mailcount"](new_count)
            mail = self.new_mails()
            self._handlers["mailnew"](mail)
        MailClient.on_run(self)

    def on_initialize(self):
        self._imap_server.login(self._username, self._password)
        old_count, new_count = self.update_counter()
        self._handlers["mailcount"](new_count)

    def on_end(self):
        pass

    def update_counter(self):
        old_count = self._count
        self._imap_server.select('INBOX')
        status, response = self._imap_server.status('INBOX', "(UNSEEN)")
        self._count = int(response[0].split()[2].strip(').,]'))
        return (old_count, self._count)

    def new_mails(self):
        status, email_ids = self._imap_server.search(None, '(UNSEEN)')
        e_id = email_ids[0].split()[-1]
        _, address = self._imap_server.fetch(e_id, '(body[header.fields (From)])')
        _, subject = self._imap_server.fetch(e_id, '(body[header.fields (Subject)])')
        address = address[0][1][6:]
        subject = subject[0][1][9:]
        address = address[address.find("<") + 1 : address.find(">")]
        self._imap_server.store(e_id,'-FLAGS','\Seen')
        return MailMessage("", address, subject, "", "")

class FacebookMail(MailClient):

    def __init__(self, session):
        MailClient.__init__(self, session)
        self._count = 0
        self._session = session
        self.facebook_client = session.facebook_client

    def on_run(self):
        if not self.facebook_client is None:
            ##sincronice facebook stuff
            self.facebook_client.process_facebook_nick()
            self.facebook_client.process_facebook_picture()
            if self._session.config.b_fb_mail_check:
                new_count = self.facebook_client.get_unread_mail_count()
                if self._count < new_count:
                    self._count = new_count
                    mail = self.new_mails()
                    self._handlers["mailnew"](mail)
                    self._handlers["mailcount"](new_count)
        MailClient.on_run(self)

    def on_initialize(self):
        if not self.facebook_client is None and self._session.config.b_fb_mail_check:
            self._count = self.facebook_client.get_unread_mail_count()
            self._handlers["mailcount"](self._count)

    def on_end(self):
        pass

    def stop(self):
        MailClient.stop(self)
        if self.facebook_client:
            self.facebook_client.active = False

    def new_mails(self):
        if not self.facebook_client is None:
            result = self.facebook_client.get_new_mail_info()
            if not result is None:
                name, body = result
                return MailMessage("", name, body, "", "")

