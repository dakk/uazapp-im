import Worker
import e3
import extension
import gobject
import os
import sys


class Session(e3.Session):
    '''a specialization of e3.Session'''
    NAME = 'WhatsApp session'
    DESCRIPTION = 'Use whatsapp protocol on your desktop'
    AUTHOR = 'Davide Gessa'
    WEBSITE = 'davidegessa.wordpress.com'

    SERVICES = {
        "whatsapp": 
        {
            "host": "dummy.server.com",
            "port": "1337"
        }
    }

    CAPABILITIES = [e3.Session.SERVICE_CONTACT_NICK,
                    e3.Session.SERVICE_STATUS,
                    e3.Session.SERVICE_CONTACT_PM,
                    e3.Session.SERVICE_CONTACT_MANAGING, 
                    e3.Session.SERVICE_CONTACT_ALIAS
                    ]

    #e3.Session.SERVICE_PROFILE_PICTURE, e3.Session.SERVICE_STATUS,

    def __init__(self, id_=None, account=None):
        '''constructor'''
        e3.Session.__init__(self, id_, account)

    def login(self, account, password, status, proxy, host, port,
              use_http=False, use_ipv6=False):
        '''start the login process'''
        self.account = e3.Account(account, password, status, host)
        worker = Worker.Worker(self, proxy, use_http, use_ipv6)
        worker.start()


        self.add_action(e3.Action.ACTION_LOGIN, (account, password, status))

    def send_message(self, cid, text, style=None, cedict=None, celist=None):
        '''send a common message'''
        if cedict is None:
            cedict = {}

        if celist is None:
            celist = []

        account = self.account.account
        message = e3.Message(e3.Message.TYPE_MESSAGE, text, account,
            style)
        self.add_action(e3.Action.ACTION_SEND_MESSAGE, (cid, message))

    def send_typing_notification(self, cid):
        '''send typing notification to contact'''
        pass

    def request_attention(self, cid):
        '''request the attention of the contact'''
        account = self.account.account
        message = e3.Message(e3.Message.TYPE_NUDGE,
            '%s requests your attention' % (account, ), account)
        self.add_action(e3.Action.ACTION_SEND_MESSAGE, (cid, message))

extension.implements(Session, 'session')
