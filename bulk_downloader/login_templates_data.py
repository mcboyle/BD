"""v3.62.2 — LOGIN templates.

Login-page selector sets, separate from the download/player TEMPLATES
in templates.py. A site config selects a login template independently
of its download template; applying one writes its block into the
site's learned.login (see app.py::_apply_login_template_by_id).

Extracted from real login-page HTML via template_extractor.
extract_login_from_html(); a few submit selectors corrected by hand
from operator-supplied page elements where a site had two same-named
submit controls in different forms.
"""
from __future__ import annotations

LOGIN_TEMPLATES = [
    {
        "id": "login_teenmegaworld",
        "name": "Teenmegaworld (login)",
        "host": "members.teenmegaworld.net",
        "login": {
            "user_field": [
                "form#form input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#form input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#form_submit",
                "button.login_button.d-flex"
            ]
        }
    },
    {
        "id": "login_brazzers",
        "name": "Brazzers (login)",
        "host": "site-ma.brazzers.com",
        "login": {
            # v3.66.6: hardened selectors. The original first-position
            # entries (form.joHetV, button.ljLTqn.hIVmej) reference
            # CSS-in-JS hashed class names that rotate on every site
            # CSS rebuild. Stable structural / attribute-based
            # fallbacks are now in front; the hashed selectors are
            # retained at the END of each list as historical hints
            # so existing operator configs that depend on them keep
            # working until they break.
            "user_field": [
                "input[name='username']",
                "input[autocomplete='username']",
                "#username",
                "form input[type='text']",
                "form.joHetV input[type='text']"
            ],
            "pass_field": [
                "input[name='password']",
                "input[autocomplete='current-password']",
                "#password",
                "form input[type='password']",
                "form.joHetV input[type='password']"
            ],
            "submit_btn": [
                "form button[type='submit']",
                "form input[type='submit']",
                "button[type='submit']",
                "button.ljLTqn.hIVmej"
            ]
        }
    },
    {
        "id": "login_ultrafilms",
        "name": "Ultrafilms (login)",
        "host": "ultrafilms.com",
        "login": {
            "user_field": [
                "form input[type='email']",
                "#inputEmail",
                "input[name='email']"
            ],
            "pass_field": [
                "form input[type='password']",
                "#inputPassword",
                "input[name='password']"
            ],
            "submit_btn": [
                "div.submit-button"
            ]
        }
    },
    {
        "id": "login_wowgirls",
        "name": "Wowgirls (login)",
        "host": "auth.wowgirls.com",
        "login": {
            "user_field": [
                "form#login-form input[type='text']",
                "#user-email",
                "input[name='username']"
            ],
            "pass_field": [
                "form#login-form input[type='password']",
                "#user-password",
                "input[name='password']"
            ],
            "submit_btn": [
                "div.loginform-submit-button"
            ]
        }
    },
    {
        "id": "login_adulttime",
        "name": "Adult Time (login)",
        "host": "freetour.adulttime.com",
        "login": {
            "user_field": [
                "form#loginForm input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#loginForm input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#submit",
                "input[name='submit']",
                "input.formElement"
            ]
        }
    },
    {
        "id": "login_vip4k",
        "name": "VIP4K (login)",
        "host": "vip4k.com",
        "login": {
            "user_field": [
                "form#login-form input[type='text']",
                "#login-username",
                "input[name='_username']"
            ],
            "pass_field": [
                "form#login-form input[type='password']",
                "#login-password",
                "input[name='_password']"
            ],
            # v3.66.6: scoped the submit selector to the login form
            # (`form#login-form`). The previous `input.button` matched
            # ANY input with class=button anywhere on the page, which
            # silently broke when the site added a second .button (an
            # "Already a member?" prompt outside the form).
            "submit_btn": [
                "form#login-form button[type='submit']",
                "form#login-form input[type='submit']",
                "form#login-form input.button",
                "input.button"
            ]
        }
    },
    {
        "id": "login_newsensations",
        "name": "New Sensations (login)",
        "host": "newsensations.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "#uid",
                "input[name='uid']"
            ],
            "pass_field": [
                "form input[type='password']",
                "#pwd",
                "input[name='pwd']"
            ],
            "submit_btn": [
                "button[name='submit']",
                "button.button--xl.button--fill"
            ]
        }
    },
    {
        "id": "login_bang",
        "name": "Bang (login)",
        "host": "www.bang.com",
        "login": {
            "user_field": [
                "form.login_form input[type='text']",
                "#username",
                "input[name='_username']"
            ],
            "pass_field": [
                "form.login_form input[type='password']",
                "#password",
                "input[name='_password']"
            ],
            "submit_btn": [
                "button[name='submit']",
                "button.text-primary-foreground.text-sm"
            ]
        }
    },
    {
        "id": "login_vixen",
        "name": "Vixen (login)",
        "host": "login.vixen.com",
        "login": {
            "user_field": [
                "form#login-form input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#login-form input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "div.f-button"
            ]
        }
    },
    {
        "id": "login_evilangel",
        "name": "Evil Angel (login)",
        "host": "www.evilangel.com",
        "login": {
            "user_field": [
                "input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#submit",
                "input[name='submit']",
                "input.formElement"
            ]
        }
    },
    {
        "id": "login_naughtyamerica",
        "name": "Naughty America (login)",
        "host": "members.naughtyamerica.com",
        "login": {
            "user_field": [
                "form.sign-in input[type='text']",
                "input[name='username']"
            ],
            "pass_field": [
                "form.sign-in input[type='password']",
                "input[name='password']"
            ],
            "submit_btn": [
                "#login",
                "input.btn"
            ]
        }
    },
    {
        "id": "login_reptyle",
        "name": "Reptyle (login)",
        "host": "auth.reptyle.com",
        "login": {
            "user_field": [
                "form#loginFormNormal input[type='email']",
                "#email",
                "input[name='email']"
            ],
            "pass_field": [
                "form#loginFormNormal input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "button[name='submit']",
                "button.button--xl.button--fill"
            ]
        }
    },
    {
        "id": "login_adulttime_2",
        "name": "Adult Time (.xxx) (login)",
        "host": "www.adulttime.xxx",
        "login": {
            "user_field": [
                "form#loginForm input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#loginForm input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#submit",
                "input[name='submit']",
                "input.formElement"
            ]
        }
    },
    {
        "id": "login_beeg",
        "name": "Beeg (login)",
        "host": "beeg.com",
        "login": {
            "user_field": [
                "form.v-form input[type='email']",
                "#input-4"
            ],
            "pass_field": [
                "form.v-form input[type='password']",
                "#input-6"
            ],
            "submit_btn": [
                "button.v-btn.v-btn--block"
            ]
        }
    },
    {
        "id": "login_filthykings",
        "name": "Filthy Kings (login)",
        "host": "www.filthykings.com",
        "login": {
            "user_field": [
                "form#loginForm input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#loginForm input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#submit",
                "input[name='submit']",
                "input.formElement"
            ]
        }
    },
    {
        "id": "login_nubiles",
        "name": "Nubiles (login)",
        "host": "nubiles.net",
        "login": {
            "user_field": [
                "form.form-signin input[type='username']",
                "#inputUsername",
                "input[name='username']"
            ],
            "pass_field": [
                "form.form-signin input[type='password']",
                "#inputPassword",
                "input[name='password']"
            ],
            "submit_btn": [
                "button[name='sign-in']",
                "button.btn.btn-primary"
            ]
        }
    },
    {
        "id": "login_nubiles_porn",
        "name": "Nubiles Porn (login)",
        "host": "members.nubiles-porn.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "input[name='username']"
            ],
            "pass_field": [
                "form input[type='password']",
                "input[name='password']"
            ],
            "submit_btn": [
                "button[name='sign-in']",
                "button.btn.btn-primary"
            ]
        }
    },
    {
        "id": "login_nubilefilms",
        "name": "Nubile Films (login)",
        "host": "nubilefilms.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "input[name='username']"
            ],
            "pass_field": [
                "form input[type='password']",
                "input[name='password']"
            ],
            "submit_btn": [
                "button[name='sign-in']",
                "button.btn.btn-primary"
            ]
        }
    },
    {
        "id": "login_kink",
        "name": "Kink (login)",
        "host": "www.kink.com",
        "login": {
            "user_field": [
                "form#loginPopup input[type='text']",
                "input[name='username']"
            ],
            "pass_field": [
                "form#loginPopup input[type='password']",
                "input[name='password']"
            ],
            "submit_btn": [
                "button.btn.btn-primary"
            ]
        }
    },
    {
        "id": "login_xempire",
        "name": "Xempire (login)",
        "host": "www.xempire.com",
        "login": {
            "user_field": [
                "form#loginForm input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#loginForm input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "#submit",
                "input[name='submit']",
                "input.formElement"
            ]
        }
    },
    {
        "id": "login_bangbros",
        "name": "Bang Bros (login)",
        "host": "site-ma.bangbros.com",
        "login": {
            # v3.66.6: hardened selectors. See login_brazzers above
            # for rationale. Bang Bros uses the same CSS-in-JS class
            # rotation pattern (form.joHetV, button.ljLTqn.*); stable
            # fallbacks are now in front.
            "user_field": [
                "input[name='username']",
                "input[autocomplete='username']",
                "#username",
                "form input[type='text']",
                "form.joHetV input[type='text']"
            ],
            "pass_field": [
                "input[name='password']",
                "input[autocomplete='current-password']",
                "#password",
                "form input[type='password']",
                "form.joHetV input[type='password']"
            ],
            "submit_btn": [
                "form button[type='submit']",
                "form input[type='submit']",
                "button[type='submit']",
                "button.ljLTqn.jWyYjI"
            ]
        }
    },
    {
        "id": "login_stepsiblingscaught",
        "name": "Stepsiblingscaught (login)",
        "host": "stepsiblingscaught.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "input[name='username']"
            ],
            "pass_field": [
                "form input[type='password']",
                "input[name='password']"
            ],
            "submit_btn": [
                "button[name='sign-in']",
                "button.btn.btn-primary"
            ]
        }
    },
    {
        "id": "login_nookies",
        "name": "Nookies (login)",
        "host": "nookies.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "input[name='uid']"
            ],
            "pass_field": [
                "form input[type='password']",
                "input[name='pwd']"
            ],
            "submit_btn": [
                "#loginBtn",
                "button.login_btn.btn"
            ]
        }
    },
    {
        "id": "login_kellymadisonmedia",
        "name": "Kelly Madison / Teen Fidelity (login)",
        "host": "members.kellymadisonmedia.com",
        "login": {
            "user_field": [
                "form.spaced input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form.spaced input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "button.button.is-medium"
            ]
        }
    },
    {
        "id": "login_pegasproductions",
        "name": "Pegas Productions (login)",
        "host": "www.pegasproductions.com",
        "login": {
            "user_field": [
                "form input[type='text']",
                "input[name='nom_util_vod']"
            ],
            "pass_field": [
                "form input[type='password']",
                "input[name='pass_vod']"
            ],
            "submit_btn": [
                "input[name='submit']"
            ]
        }
    },
    {
        "id": "login_account_dorcel",
        "name": "Dorcel (login)",
        "host": "www.account-dorcel.com",
        "login": {
            "user_field": [
                "form#form input[type='text']",
                "#username",
                "input[name='username']"
            ],
            "pass_field": [
                "form#form input[type='password']",
                "#password",
                "input[name='password']"
            ],
            "submit_btn": [
                "form#form button[type='submit'][value='Confirm']",
                "form#form button[type='submit']"
            ]
        }
    },
    {
        "id": "login_tiny4k",
        "name": "Tiny4K (login)",
        "host": "tiny4k.com",
        "login": {
            "user_field": [
                "form.w-full input[type='text']"
            ],
            "pass_field": [
                "form.w-full input[type='password']"
            ],
            "submit_btn": [
                "button.app-button.app-button--white"
            ]
        }
    }
]


def list_login_templates():
    """Return all login templates (id, name, host) for UI pickers."""
    return [{"id": t["id"], "name": t["name"], "host": t["host"]}
            for t in LOGIN_TEMPLATES]


def get_login_template(tid):
    """Return the full login template dict for an id, or None."""
    for t in LOGIN_TEMPLATES:
        if t["id"] == tid:
            return t
    return None


def suggest_login_for_url(url):
    """v3.65.2: Given a URL (or bare hostname), return matching login
    template IDs ordered by relevance.

    Match strategy:
      1. Exact host match — `host` equals the URL's hostname.
      2. Suffix match — `host` is a dotted suffix of the URL's hostname
         (so a template host=`brazzers.com` matches
         `members.brazzers.com` AND `www.brazzers.com`).
      3. Substring match as a defensive fallback for malformed inputs.

    Empty list when nothing matches. The caller (auto-pick at site
    creation) takes the top entry; the UI uses the full list to
    surface suggestions in the picker.
    """
    if not url:
        return []
    # Allow bare hostnames as input — urlparse needs a scheme to
    # populate netloc, so handle that defensively.
    s = str(url).strip().lower()
    host = ""
    try:
        from urllib.parse import urlparse
        if "://" in s:
            host = (urlparse(s).hostname or "").lower()
        elif "/" in s:
            host = s.split("/", 1)[0]
        else:
            host = s
    except Exception:
        host = s
    if not host:
        return []

    exact = []
    suffix = []
    domain = []
    sub = []

    def _reg_domain(h):
        # Cheap registered-domain heuristic: last 2 dotted parts.
        # Doesn't know the public suffix list (so "site.co.uk" → "co.uk"
        # which is wrong), but for the .com/.net/.tv hosts in the
        # login template index this is fine.
        parts = (h or "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h

    host_rd = _reg_domain(host)
    for t in LOGIN_TEMPLATES:
        th = (t.get("host") or "").lower().strip()
        if not th:
            continue
        if host == th:
            exact.append(t["id"])
        elif host.endswith("." + th) or th.endswith("." + host):
            # `host=members.brazzers.com` matches template host=`brazzers.com`
            # AND vice versa (template host=`site-ma.brazzers.com` should
            # still match a user-supplied login_url on brazzers.com root,
            # because the saved selectors usually work cross-subdomain on
            # the same site network).
            suffix.append(t["id"])
        elif host_rd and _reg_domain(th) == host_rd:
            # Registered-domain match (e.g. user typed `www.brazzers.com`,
            # template is `site-ma.brazzers.com`). Lower confidence than
            # the suffix match above but still likely the right network.
            domain.append(t["id"])
        elif th in host or host in th:
            sub.append(t["id"])
    # Preserve order, dedupe via dict.fromkeys
    return list(dict.fromkeys(exact + suffix + domain + sub))
