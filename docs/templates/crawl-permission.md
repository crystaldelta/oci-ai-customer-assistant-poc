<!--
Email to the client asking for written permission to fetch their website content. Send after the
site survey (docs/website-content.md, step 1), which gives the sections, page count and any bot
protection or AI-bot rules to mention, and before any crawl. Record the reply (date, who, scope)
in PLAN.md as a settled decision. Delete this comment when copying.
-->

**Subject:** Permission to read <domain> for the <assistant name>

Hi <name>,

To answer customers' questions from your website, the assistant needs a copy of its public
pages. Could you confirm in writing that we may fetch them, within the limits below?

**What we would fetch**

- Public pages only, in these sections: <list>. No pages behind a login, and no forms or quote
  tools.
- About <n> pages for the first copy; after that, only pages the sitemap marks as changed, up to
  <twice a day>. An unchanged site costs two requests.

**How we fetch them**

- One request at a time, with a random pause of about a second between requests.
- Our crawler names itself (`<user agent>`) and follows your `robots.txt`.
- <If the survey saw bot protection:> Your site uses <bot protection>. Should we ask your team
  to allow our crawler, so that it is not mistaken for a bot you want to block?
- <If robots.txt has rules for AI crawlers:> We noticed your `robots.txt` limits some AI
  crawlers. Ours is not one of them, but we would like to confirm this use suits your policy.
- If your site refuses us (for example your bot protection), we stop at once and contact you; we
  never work around it.
- From <fixed address, if a NAT is used>, if you would like to allowlist it.

**What we do with the content**

- It is used only to answer questions in the assistant, and stored in <cloud, region>.
- Each answer links back to the page it came from.

Could you also let us know whether anyone else, such as your web or security team, should know
before we start?

Kind regards,
<name>
