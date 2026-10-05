<!--
Email to the client asking for written permission to fetch their website content. Send before
any request to the site. Record the reply (date, who, scope) in PLAN.md as a settled decision.
Delete this comment when copying.
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
