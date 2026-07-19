<!-- verified-against: v3.66.805 -->
# Sanctioned test URLs — BulkDownloader harness allowlists

Ethics floor: public, non-adult, purpose-built demo/test/practice endpoints only.
Challenge sites are DETECTED and handed off, never solved. Published-credential
practice sites are used as authorized (a login is not a challenge). Adult registry
sites are off-limits (player-family detection is site-agnostic; synthetic fixtures
suffice). Off-allowlist URLs are REFUSED by every harness.

## bd-corpus players
reference.dashif.org, hlsjs.video-dev.org, shaka-player-demo.appspot.com,
docs.flowplayer.com, vidstack.io, dplayer.diygod.dev, mediaelementjs.com,
tools.axinom.com/players/theoplayer, videojs.org, bitmovin.com, artplayer.org.

## bd-corpus auth (LOGIN-FORM detect only, never submits)
the-internet.herokuapp.com/login, practicetestautomation.com/practice-test-login,
practice.expandtesting.com/login, parabank.parasoft.com, automationexercise.com.

## bd-corpus challenge (DETECT -> handoff ONLY; official vendor test pages)
developers.cloudflare.com/turnstile/troubleshooting/testing,
recaptcha-demo.appspot.com, accounts.hcaptcha.com/demo, altcha.org.

## bd-dltest media (public-domain / CC / vendor-official)
test-videos.co.uk, devstreaming-cdn.apple.com (bipbop), test-streams.mux.dev,
dash.akamaized.net, archive.org, commondatastorage.googleapis.com.

## bd-runner-nav SSRF negatives (MUST be refused, never fetched)
127.0.0.1, 169.254.169.254 (metadata), 10.x, [::1].

## Excluded on principle
Captcha-solving services (2captcha etc). Adult registry/tube sites. Coverr/Pexels.
