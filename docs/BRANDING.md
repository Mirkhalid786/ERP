# Branding & Appearance

## Who can change what
| Change | Where | Permission |
|---|---|---|
| App name, short name, tagline, login welcome, website | Admin → Branding & appearance (`/settings/branding`) | `settings.manage` |
| Logos (main, dark-mode, compact, favicon), logo fit, text-only brand | same page | `settings.manage` |
| Business name, legal name, address, phone, email (printed on invoices) | same page | `settings.manage` |
| Invoice footer / thank-you text, receipt footer | same page | `settings.manage` |
| **Default** theme, mode, colours, navigation, density, dashboard layout | same page | `settings.manage` |
| **Own** theme, mode, colours, navigation, density, dashboard layout | Palette icon in the sidebar → My appearance (`/settings/appearance`) | any signed-in user |
| Reset another user's appearance | Users → palette icon on the user's row | `users.manage` |

GSTIN, drug licence and state are tax identity: they are set at first-run setup and shown read-only on the branding page.
Every global change (identity, logos, appearance defaults, another user's reset) is written to the audit log.

## How the two layers combine
- The pharmacy default applies to new users and to anyone who has not chosen their own appearance.
- A user's saved choices override the default **for that user only**. Only the fields the user changed from the
  default are stored, so later changes to the default still reach everything the user did not customise.
- A user's choice never changes the default or anyone else. "Use pharmacy default" on My appearance (or the
  administrator's reset) removes the user's own choices.
- The login page always shows the pharmacy default, because nobody is signed in yet.

## Themes
Emerald Green + White (default), Blue + White, Teal + White, Purple + White, Slate / Neutral, Rose / Burgundy,
High contrast, Dark Emerald, Dark Blue, Dark Neutral. Light, dark and system mode can be combined with any preset.

Custom colours (all optional, `#RRGGBB` only): primary, secondary, accent, page background, cards, sidebar, text,
muted text, borders, success, warning, error, information. The editor warns when text would be hard to read.

Layout: sidebar (collapsible) or top-bar navigation, comfortable or compact density, and the order and visibility of
dashboard sections. Hiding a dashboard section only changes the layout; it never grants access to data.

## Logos
- PNG, JPEG or WebP only, identified by their content (not the file name). SVG is not accepted.
- Size limits: main and dark-mode logo 1 MB, compact logo 512 KB, favicon 256 KB; at most 4096 px per side.
- Stored in the database table `brand_assets`, so they are included in every backup and survive container rebuilds.
  No Docker volume is needed. They are served from `/api/v1/branding/assets/<kind>` with `nosniff` and a sandbox CSP.
- The main logo, the compact logo and the favicon are public, because the login page and browser tab need them.

## Invoices and receipts
- They print black on white whatever theme the screen uses.
- The main logo prints at the top of A4 invoices and, in greyscale, on 80 mm receipts.
- The receipt footer is used on receipts; when it is empty, the invoice footer is used.
- Invoices are rendered from the current business details when printed (this was already the case), so a changed
  address also shows when an old invoice is reprinted. Amounts, numbers, tax and payments are never changed.

## Data sent to signed-out visitors
`GET /api/v1/branding/public` returns only: app name, short name, tagline, login welcome, logo fit, text-only flag,
the default appearance and logo versions. No address, phone, email, website, footers or tax details.

## Storage
- Text settings: the `settings` table, keys `branding.*`. The invoice footer is the existing `invoice.footer_note` setting.
- User choices: the `user_preferences` table (migration 011).
- Logos: the `brand_assets` table (migration 011).
- The public branding is cached in the API for up to 60 seconds and refreshed immediately when it changes.
