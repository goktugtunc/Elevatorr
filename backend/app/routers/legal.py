"""Public legal pages (Google Play submission). Mounted at the root, no auth, no database.

Play requires publicly reachable URLs for the privacy policy and for account deletion,
and the store listing links to the data-safety and financial disclosures. These are
served as plain HTML from the API host so there is no second thing to deploy.

Everything stated here is derived from the code, not aspiration:
  * the platform never holds a user's private key — the server builds unsigned EVM
    transactions (calldata) and the user's own wallet signs them (see services/chain, routers/tx),
  * no email, phone, location or KYC field exists on the user model,
  * no analytics, advertising or attribution SDK is bundled in the client.
Change the code first, then this page.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.core.config import Settings, get_settings

router = APIRouter(tags=["legal"], include_in_schema=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]

APP_NAME = "TraderKirala"
UPDATED = "26 September 2026"

_CSS = """
:root{--ink:#0B1F3A;--muted:#5A6B84;--line:#E3E8F0;--bg:#F7F9FC;--accent:#0B1F3A}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:40px 20px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:32px}
h1{font-size:30px;line-height:1.25;margin:0 0 6px}
h2{font-size:19px;margin:34px 0 10px}
h3{font-size:16px;margin:22px 0 6px}
p,li{color:#22324a}
.meta{color:var(--muted);font-size:14px;margin:0}
a{color:#1355C4}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:15px}
th,td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}
th{background:#EEF2F8;font-weight:600}
.box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:18px 0}
.warn{background:#FFF6F5;border-color:#F3C9C4}
code{background:#EEF2F8;padding:1px 5px;border-radius:4px;font-size:14px}
footer{margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:14px}
ul{padding-left:20px}
"""


def _page(title: str, body: str, contact: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {APP_NAME}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<header><h1>{title}</h1><p class="meta">{APP_NAME} · Last updated {UPDATED}</p></header>
{body}
<footer>
<p>Contact: {contact}</p>
<p><a href="/legal">All documents</a> · <a href="/legal/privacy">Privacy</a>
 · <a href="/legal/terms">Terms</a> · <a href="/legal/data-safety">Data safety</a>
 · <a href="/legal/financial-disclosure">Financial disclosure</a>
 · <a href="/legal/delete-account">Delete account</a></p>
</footer>
</div></body></html>""")


def _contact(settings: Settings) -> str:
    value = getattr(settings, "legal_contact_email", None)
    if value:
        return f'<a href="mailto:{value}">{value}</a>'
    return ('<strong style="color:#B3261E">Set LEGAL_CONTACT_EMAIL in the server .env '
            "— Google Play requires a working contact address.</strong>")


@router.get("/legal", response_class=HTMLResponse)
async def index(settings: SettingsDep) -> HTMLResponse:
    return _page("Legal & disclosures", """
<p>These pages describe what {app} does with your data, what it does not do, and the
risks of using it. They are the documents linked from the Google Play listing.</p>
<div class="box">
<h3 style="margin-top:0">Documents</h3>
<ul>
<li><a href="/legal/privacy">Privacy Policy</a> — what is stored and why</li>
<li><a href="/legal/terms">Terms of Service</a> — the rules of using the app</li>
<li><a href="/legal/data-safety">Data Safety summary</a> — the Play Data safety answers</li>
<li><a href="/legal/financial-disclosure">Financial features &amp; risk disclosure</a></li>
<li><a href="/legal/delete-account">Account &amp; data deletion</a></li>
</ul>
</div>
<div class="box warn">
<h3 style="margin-top:0">Test network only</h3>
<p>{app} currently runs on the <strong>Monad test network</strong> (Monad Testnet). Test tokens
and test MON have no monetary value and cannot be exchanged for money. Nothing in the app moves real funds.</p>
</div>
""".replace("{app}", APP_NAME), _contact(settings))


@router.get("/legal/privacy", response_class=HTMLResponse)
async def privacy(settings: SettingsDep) -> HTMLResponse:
    return _page("Privacy Policy", """
<p>{app} connects people who own capital with traders who manage it, with the money held
in a smart contract on the Monad network. This policy explains exactly what the service
stores. It describes the system as built; it is not a statement of intent.</p>

<h2>1. Who we are</h2>
<p>{app} ("the app", "we") is operated by the developer identified in the Google Play
listing. Questions about this policy go to the contact address at the bottom of this page.</p>

<h2>2. There is no account in the usual sense</h2>
<p>You do not create an account with an email address and a password. You sign in by
connecting an EVM wallet (for example MetaMask) and signing a one-time Sign-In with Ethereum
message (EIP-4361). Signing that message costs no gas and moves nothing. Your identity in the app
is your public wallet address.</p>
<div class="box">
<p style="margin:0"><strong>Your private key never reaches us.</strong> The server prepares
unsigned transactions; your wallet app signs them on your device and only the signed result
is submitted. We cannot move your funds, and we cannot sign anything on your behalf.</p>
</div>

<h2>3. What we store</h2>
<table>
<tr><th>Data</th><th>Why</th></tr>
<tr><td>Public wallet address (0x…)</td><td>Identifies your account and your on-chain activity.</td></tr>
<tr><td>Username, display name</td><td>Shown to other users on listings and messages.</td></tr>
<tr><td>Profile text you write — bio, strategy summary, track record</td><td>Shown on your public profile so the other side can judge you.</td></tr>
<tr><td>Avatar image URL, if you set one</td><td>Shown next to your name.</td></tr>
<tr><td>Your stated budget, risk preference and markets</td><td>Used to match listings to you.</td></tr>
<tr><td>Listings, offers, agreements, trades and ratings</td><td>The core function of the service.</td></tr>
<tr><td>Messages you send to other users</td><td>Delivered to the person you are talking to.</td></tr>
<tr><td>Notification token, if you enable notifications</td><td>Used only to deliver push notifications about your own agreements.</td></tr>
<tr><td>Last sign-in time</td><td>Operational: abuse handling and support.</td></tr>
</table>

<h2>4. What we do not collect</h2>
<ul>
<li>No email address, no phone number.</li>
<li>No location data, no contacts, no calendar, no photos or media library access.</li>
<li>No identity documents and no KYC data — the app performs no identity verification.</li>
<li>No advertising identifier. There is <strong>no advertising, analytics, attribution or
crash-reporting SDK</strong> in the app.</li>
<li>No browsing history outside the app.</li>
</ul>

<h2>5. Who the data is shared with</h2>
<p>We do not sell data and we do not share it for advertising. Data leaves the service only where
the function requires it:</p>
<ul>
<li><strong>The Monad network.</strong> Transactions, balances and contract state are public
by the nature of a blockchain. Your public address and your on-chain activity are visible to
anyone, permanently, including after you delete your account here.</li>
<li><strong>Other users.</strong> Your profile, listings, ratings and the messages you send are
shown to the people you interact with.</li>
<li><strong>Your wallet provider</strong> (for example MetaMask) and the WalletConnect relay,
which carry the signing requests between the app and your wallet.</li>
<li><strong>Push delivery</strong> (Expo) if you enable notifications.</li>
</ul>

<h2>6. How long it is kept</h2>
<p>Profile and activity data are kept while your account exists. When you delete your account
(see <a href="/legal/delete-account">Account &amp; data deletion</a>) the profile, messages and
notification token are removed. <strong>On-chain records cannot be deleted by us or by anyone
else</strong> — a blockchain is append-only.</p>

<h2>7. Security</h2>
<ul>
<li>Traffic between the app and the server is encrypted with TLS.</li>
<li>Authentication is a signed message (Sign-In with Ethereum, EIP-4361); there is no password to leak.</li>
<li>The server holds no user secret keys, so a breach of the server cannot move user funds.</li>
</ul>

<h2>8. Children</h2>
<p>The service is not directed at children and is not intended for anyone under 18.</p>

<h2>9. Your rights</h2>
<p>You can view and edit your profile in the app, export your data by request, and delete your
account. For anything else, write to the contact address below.</p>

<h2>10. Changes</h2>
<p>If this policy changes materially the date at the top of the page changes and the app shows
a notice.</p>
""".replace("{app}", APP_NAME), _contact(settings))


@router.get("/legal/terms", response_class=HTMLResponse)
async def terms(settings: SettingsDep) -> HTMLResponse:
    return _page("Terms of Service", """
<h2>1. What the service is</h2>
<p>{app} is a marketplace and a set of tools. Capital owners publish how much they are willing
to put to work and on what terms; traders publish how they trade. When both sides agree, the
capital is locked in a smart contract on the Monad network and the trader can swap it inside
that contract but can never withdraw it.</p>
<p>We are not a broker, a bank, an exchange, a fund or an investment adviser. We do not manage
money, we do not take custody of it, and we give no investment advice or recommendations.</p>

<h2>2. Test network</h2>
<p>The service currently operates on the Monad <strong>test network</strong> (Monad Testnet). Test
tokens (tUSDC, tWETH, tWBTC) and test MON are issued freely for development, have no monetary value
and cannot be redeemed for money.</p>

<h2>3. Your wallet is yours</h2>
<p>You keep your own keys. Every on-chain action is signed on your device. If you lose access to
your wallet we cannot recover it, reverse a transaction or restore funds.</p>

<h2>4. What you agree to</h2>
<ul>
<li>You are at least 18 and using the service is lawful where you live.</li>
<li>The information you publish about yourself — track record, strategy, returns — is truthful.
Fabricating a track record is grounds for removal.</li>
<li>You will not use the service for market manipulation, money laundering, or to interact with
sanctioned parties.</li>
<li>You will not attempt to attack the contract, the API or other users.</li>
</ul>

<h2>5. Agreements between users</h2>
<p>An agreement is between the capital owner and the trader. Its terms — amount, duration,
commission, maximum loss — are written into the smart contract when the agreement is funded.
The contract, not us, enforces them. We do not guarantee any outcome and are not a party to
the agreement.</p>

<h2>6. Fees</h2>
<p>The trader's commission is the percentage agreed in the offer and is paid out of profit only.
The platform fee is published in the app and is currently <strong>zero</strong>. Monad network
fees (gas, paid in MON) are paid by whoever signs the transaction.</p>

<h2>7. No guarantee of returns</h2>
<p>Expected-return figures shown on listings are estimates written by the trader, not promises.
Trading loses money as easily as it makes it. See the
<a href="/legal/financial-disclosure">risk disclosure</a>.</p>

<h2>8. Availability</h2>
<p>The service is provided as-is. We may change or suspend it. Blockchain networks and wallets
are operated by third parties and can fail independently of us.</p>

<h2>9. Suspension</h2>
<p>We may suspend an account that breaks these terms. Funds already locked in a contract remain
governed by the contract, not by us.</p>

<h2>10. Liability</h2>
<p>To the extent the law allows, we are not liable for trading losses, for the acts of a trader
or a capital owner, for the behaviour of a third-party wallet or network, or for loss of
keys.</p>
""".replace("{app}", APP_NAME), _contact(settings))


@router.get("/legal/data-safety", response_class=HTMLResponse)
async def data_safety(settings: SettingsDep) -> HTMLResponse:
    return _page("Data Safety summary", """
<p>This mirrors the answers given in the Google Play <em>Data safety</em> form, so a reviewer or
a user can check that the form matches reality.</p>

<h2>Collected and linked to you</h2>
<table>
<tr><th>Type</th><th>Purpose</th><th>Optional?</th></tr>
<tr><td>User IDs (public wallet address, username)</td><td>App functionality, account management</td><td>Required</td></tr>
<tr><td>Name (display name)</td><td>App functionality</td><td>Required</td></tr>
<tr><td>Other user-generated content (bio, strategy, track record, listings, offers)</td><td>App functionality</td><td>Optional</td></tr>
<tr><td>Messages between users</td><td>App functionality</td><td>Optional</td></tr>
<tr><td>Other financial info (stated budget, risk preference, agreement amounts)</td><td>App functionality</td><td>Required for the relevant feature</td></tr>
<tr><td>Photos (avatar URL only, if you set one)</td><td>App functionality</td><td>Optional</td></tr>
</table>

<h2>Not collected</h2>
<ul>
<li>Email address, phone number, physical address</li>
<li>Location, contacts, calendar, SMS, call logs, microphone, camera</li>
<li>Device or advertising identifiers</li>
<li>Payment card, bank account or government ID data</li>
<li>App activity analytics, crash logs or diagnostics sent to us</li>
</ul>

<h2>Handling</h2>
<ul>
<li><strong>Encrypted in transit:</strong> yes, TLS.</li>
<li><strong>Data is not sold</strong> and is not shared with third parties for advertising or
marketing.</li>
<li><strong>Deletion:</strong> you can request account and data deletion —
see <a href="/legal/delete-account">Account &amp; data deletion</a>.</li>
<li><strong>Third-party SDKs:</strong> none for ads, analytics, attribution or crash reporting.</li>
</ul>

<div class="box warn">
<h3 style="margin-top:0">Blockchain caveat</h3>
<p style="margin:0">Data written to the Monad network — your public address, transactions and
contract state — is public and permanent. It is outside our control and cannot be deleted.</p>
</div>

<h2>Permissions the app requests</h2>
<table>
<tr><th>Permission</th><th>Why</th></tr>
<tr><td>Internet</td><td>Talk to the API, the Monad network and your wallet.</td></tr>
<tr><td>Notifications</td><td>Tell you when an offer arrives or an agreement changes. Optional.</td></tr>
<tr><td>Vibrate</td><td>Notification feedback.</td></tr>
<tr><td>Biometric / fingerprint</td><td>Optionally lock the app on your device. Nothing biometric leaves the device.</td></tr>
</table>
""".replace("{app}", APP_NAME), _contact(settings))


@router.get("/legal/financial-disclosure", response_class=HTMLResponse)
async def financial(settings: SettingsDep) -> HTMLResponse:
    return _page("Financial features & risk disclosure", """
<div class="box warn">
<h3 style="margin-top:0">Read this before using the app</h3>
<p style="margin:0">Trading digital assets can lose money, including all of it. Nothing in
{app} is a promise of profit. Past results of a trader do not predict future results.</p>
</div>

<h2>1. What {app} is — and is not</h2>
<table>
<tr><th>It is</th><th>It is not</th></tr>
<tr><td>A marketplace where capital owners and traders find each other</td><td>A broker, bank, exchange or payment institution</td></tr>
<tr><td>A front end to a smart contract that holds capital in escrow</td><td>A custodian — we never hold your keys or your funds</td></tr>
<tr><td>A place to publish and read self-declared track records</td><td>An investment adviser; nothing here is a recommendation</td></tr>
<tr><td>A tool that shows live market data</td><td>A fund, a pooled investment scheme, or a yield product</td></tr>
</table>

<h2>2. Custody</h2>
<p><strong>{app} is non-custodial.</strong> Your private key stays in your own wallet on your own
device. The server builds unsigned transactions; your wallet signs them. The platform cannot
withdraw, move or freeze user funds, and holds no user secret key. Capital committed to an
agreement sits in a smart contract on Monad (an EVM chain), not in a company account.</p>

<h2>3. What the trader can and cannot do</h2>
<ul>
<li>A trader can <strong>swap</strong> the capital between assets inside the contract, through an
allow-listed router.</li>
<li>A trader can <strong>never withdraw</strong> the capital. There is no contract path that sends
it anywhere but back to the capital owner at settlement.</li>
<li>Every agreement carries a <strong>maximum-loss floor</strong>. The contract rejects a trade
that would push the portfolio below it.</li>
<li>Commission is paid <strong>out of profit only</strong>. On a loss the trader is paid nothing.</li>
</ul>

<h2>4. Test network</h2>
<p>The app currently operates on the Monad <strong>test network</strong> (Monad Testnet). Test tokens
and test MON are issued freely, have no monetary value and cannot be converted into money. No real
payment is taken and no real funds are at risk.</p>

<h2>5. Deposits and withdrawals</h2>
<p>{app} offers no fiat deposit or withdrawal and takes no payment. Tokens enter and leave only
through your own wallet. On the test network the app can hand out test tokens (tUSDC, tWETH, tWBTC)
from a faucet; they have no value and cannot be redeemed.</p>

<h2>6. No advice, no guarantee</h2>
<p>Expected-return ranges on a listing are written by the trader as an estimate. They are not
verified by us, not guaranteed, and not a promise. The rating and statistics on a profile are
derived from activity inside the app and can be thin or unrepresentative.</p>

<h2>7. Fees</h2>
<p>The trader's commission is agreed per contract and is taken from profit. The platform fee is
shown in the app and is currently <strong>zero</strong>. Monad charges a network fee (gas, in MON)
on every transaction, paid by whoever signs it.</p>

<h2>8. Regulatory status</h2>
<p>{app} is an independent software project. It is not licensed as a financial institution and
does not provide regulated financial services. You are responsible for whether using it is
lawful where you live and for any tax arising from your activity.</p>
""".replace("{app}", APP_NAME), _contact(settings))


@router.get("/legal/delete-account", response_class=HTMLResponse)
async def delete_account(settings: SettingsDep) -> HTMLResponse:
    return _page("Account & data deletion", """
<p>You can delete your {app} account and the data attached to it. Google Play requires this
route to be reachable without installing the app, which is why it is on this page.</p>

<h2>In the app</h2>
<ol>
<li>Open <strong>Profile</strong>.</li>
<li>Choose <strong>Disconnect wallet</strong> to end the session, or
<strong>Reset app data</strong> to clear everything stored on the device.</li>
<li>To remove the server-side account as well, send the request described below.</li>
</ol>

<h2>By request</h2>
<p>Write to the contact address at the bottom of this page from the account you want removed, or
include your public wallet address, with the subject <code>Account deletion</code>. The account
is removed within 30 days and you receive a confirmation.</p>

<h2>What is deleted</h2>
<ul>
<li>Profile: username, display name, bio, strategy summary, track record, avatar, preferences.</li>
<li>Your notification token, so notifications stop immediately.</li>
<li>Messages you sent, and your listings that have never become an agreement.</li>
</ul>

<h2>What is kept, and why</h2>
<ul>
<li><strong>On-chain records cannot be deleted.</strong> Transactions and contract state on the
Monad network are public and permanent — by us, by you, by anyone.</li>
<li>Records of a <strong>settled agreement</strong> are kept in a minimal form, because the other
party has the same legal claim to their copy of that history.</li>
</ul>

<div class="box warn">
<p style="margin:0"><strong>Settle first.</strong> Deleting the account does not close an open
agreement and does not release capital from the contract. Settle or cancel your agreements
before requesting deletion.</p>
</div>
""".replace("{app}", APP_NAME), _contact(settings))
