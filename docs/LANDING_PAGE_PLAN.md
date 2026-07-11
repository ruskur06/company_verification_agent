# Multilingual Landing Page Plan

## 1. Goal

Create a multilingual landing page for Company Verification Agent.

The landing page must explain how the service helps users perform a preliminary verification of foreign companies before important financial, commercial, legal, or real-estate transactions.

The landing page must not promise complete legal due diligence or guarantee that a company or transaction is safe.

---

## 2. Target Audiences

### Real Estate Investors

Users considering investments in foreign:

* residential property;
* commercial property;
* offices;
* warehouses;
* hotels;
* land;
* other investment properties.

Companies that may be checked:

* property sellers;
* developers;
* real-estate agencies;
* management companies;
* intermediaries;
* companies involved in the transaction.

### Importers

Small importers reviewing a foreign supplier, manufacturer, exporter, or commercial counterparty before placing an order or making a payment.

### Procurement Teams

Procurement specialists and companies beginning work with a new foreign supplier or international counterparty.

### Lawyers and Advisors

Lawyers, legal consultants, and professional advisors performing preliminary company research for international transactions and client due diligence.

---

## 3. Main Positioning

### Main headline

Verify a foreign company before making an important deal

### Main description

Collect and compare information from official registries, company websites, and open sources. See what was confirmed, what does not match, and what requires additional official verification.

### Primary call to action

Check a company

### Secondary call to action

View sample report

---

## 4. Trust Principle

The service must not claim:

* We check everything
* Complete company verification
* Guaranteed safe company
* Complete due diligence
* Zero-risk investment

The main principle is:

> We show what can be verified automatically, what depends on the country, and what requires an additional official request.

For relevant findings, the service should show:

* source;
* retrieval date;
* information found;
* verification status;
* confidence level;
* source limitations;
* required follow-up checks.

---

## 5. Landing Page Sections

### Section 1 — Hero

Content:

* main headline;
* short product explanation;
* primary CTA;
* secondary CTA;
* visual preview of a company verification report.

### Section 2 — Who Is It For?

Cards:

* Real Estate Investors
* Importers
* Procurement Teams
* Lawyers and Advisors

### Section 3 — Why Company Verification Matters

Questions:

* Does the company legally exist?
* Is its registration status current?
* Does the company website belong to the same legal entity?
* Do the company name and address match official records?
* Are different sources referring to the same organization?
* Are important records missing or unavailable?
* Is an official document required before proceeding?

### Section 4 — How It Works

Step 1:

Enter the company name, country, and website if available.

Step 2:

The system searches supported registries and open sources.

Step 3:

Verification agents compare company names, registration details, websites, and other available signals.

Step 4:

Receive a structured report with sources, findings, inconsistencies, and items requiring additional verification.

---

## 6. Data Access Levels

### Main heading

We do not pretend to know more than the registry

### Description

Different countries publish different amounts of company information. We show the source, retrieval date, and confidence level for relevant records and clearly indicate when an official request is needed.

### Level 1 — Automatically and Immediately

Depending on source availability, the service may check:

* official company name;
* registration number;
* company status;
* registration date;
* registered address;
* alternative company names;
* online presence;
* company website;
* website availability;
* website and company relationship;
* differences between official and public information.

### Level 2 — Automatically Where Supported

The service may use:

* national company registries;
* government open data;
* supported international registry systems;
* authorized public sources.

The depth of available information differs by jurisdiction.

Some countries publish detailed company records, management information, company history, and documents.

Other countries provide only basic registration information.

Missing data must not be replaced with assumptions.

Limited registry information must not be presented as complete legal due diligence.

### Level 3 — Requires an Official Request

Additional verification may require:

* official company extracts;
* paid registry documents;
* land and property records;
* cadastral information;
* ownership documents;
* restricted court records;
* identity verification;
* requests through a lawyer;
* requests through a notary;
* official government procedures.

The service does not bypass access restrictions and does not present automated findings as official legal documents.

The report should explain:

* which information is missing;
* why it is not available automatically;
* which document may be required;
* which official request may be necessary;
* which additional professional verification should be considered.

---

## 7. User-Facing Source Statuses

Internal technical statuses must not be displayed directly.

### candidates_found

User-facing status:

Match found

Description:

Registry information matching the supplied company details was found.

### no_candidates

User-facing status:

No matching company was found using the supplied information

Description:

This does not automatically mean that the company does not exist. The company name, registration number, or search method may require clarification.

### unavailable

User-facing status:

Source temporarily unavailable

Description:

The registry or external source could not be reached during this check. This is not treated as a negative finding.

### disabled

User-facing status:

Automatic verification is not currently supported

Description:

Automatic access to this source or jurisdiction is not yet available.

### configuration_error

Do not display this technical status to the user.

Store it in:

* application logs;
* internal audit records;
* technical monitoring.

---

## 8. Report Preview

The landing page should contain a realistic example report.

Example:

Company:

Example Development GmbH

Country:

Austria

Registry match:

Found

Company status:

Active

Website relationship:

Additional verification required

Sources reviewed:

6

Verification confidence:

Medium

Items requiring official documentation:

2

CTA:

View sample report

---

## 9. Source Audit Trail

Heading:

See where every result came from

The report should show:

* source name;
* source type;
* access date and time;
* source result;
* relevant information found;
* source limitations;
* required follow-up checks.

Main message:

Users receive more than a final score. They can see which sources were reviewed and understand what each conclusion is based on.

---

## 10. Final Call to Action

Heading:

Check the company before making the decision

Description:

Start with a structured preliminary review of available company records, official sources, and public information.

Button:

Start company check

---

## 11. Legal Disclaimer

The service provides preliminary company verification based on available registry data, official sources, and publicly accessible information.

It does not replace legal, financial, tax, real-estate, or professional due diligence.

---

## 12. Languages

Initial release:

* English
* German
* Spanish

English is the source language.

Russian is not included in the initial release.

Hebrew is not included in the initial release.

---

## 13. URLs

English:

/en

German:

/de

Spanish:

/es

Root:

/

Redirects to:

/en

---

## 14. Technical Architecture

Use:

* one HTML template;
* one CSS file;
* one JavaScript file if required;
* one translation file per language.

Do not create separate HTML templates for each language.

Planned structure:

```text
app/
├── web/
│   ├── templates/
│   │   └── landing.html
│   ├── static/
│   │   ├── landing.css
│   │   └── landing.js
│   └── translations/
│       ├── en.json
│       ├── de.json
│       └── es.json
│
tests/
├── test_landing_page.py
└── test_landing_translations.py
```

The final structure may be adjusted after inspecting the existing FastAPI routes and web layer.

---

## 15. Translation Rules

English is the source language.

Translation order:

1. Approve English content.
2. Create stable translation keys.
3. Create the German localization.
4. Create the Spanish localization.
5. Review business terminology.
6. Check text length in cards and buttons.
7. Test desktop and mobile layouts.

Translations should not be literal.

German text should sound natural in a professional German-language business interface.

Spanish text should remain neutral and understandable in Spain and Latin America.

---

## 16. Initial Release Scope

Included:

* English;
* German;
* Spanish;
* responsive layout;
* product explanation;
* target audience cards;
* verification workflow;
* three data-access levels;
* user-friendly source statuses;
* report preview;
* source audit trail;
* language selector;
* integration with the existing company-check workflow;
* legal disclaimer.

Not included:

* payment;
* pricing;
* user accounts;
* personal dashboard;
* blog;
* chat;
* automatic country detection;
* Russian;
* Hebrew;
* separate frontend framework.

---

## 17. Minimum Tests

Required checks:

* `/` redirects to `/en`;
* `/en` returns HTTP 200;
* `/de` returns HTTP 200;
* `/es` returns HTTP 200;
* unsupported language does not cause HTTP 500;
* all required translation keys exist in every language;
* language selector contains EN, DE, and ES;
* primary CTA opens the existing company-check flow;
* each page contains the correct HTML `lang` attribute;
* pages contain appropriate language links;
* raw internal source statuses are not shown;
* `configuration_error` is not displayed to users.

---

## 18. Development Phases

### Phase 1 — Documentation and English Content

Deliverables:

* landing-page plan;
* approved English copy;
* translation key structure.

### Phase 2 — FastAPI Routes and Template

Deliverables:

* `/`;
* `/en`;
* `/de`;
* `/es`;
* one shared HTML template.

### Phase 3 — Responsive Design

Deliverables:

* desktop layout;
* tablet layout;
* mobile layout.

### Phase 4 — German and Spanish Localization

Deliverables:

* German translation;
* Spanish translation;
* terminology review.

### Phase 5 — Existing Product Integration

Deliverables:

* primary CTA connected to the existing company-check workflow;
* sample-report navigation.

### Phase 6 — Automated Tests

Deliverables:

* route tests;
* translation tests;
* language tests;
* CTA tests.

### Phase 7 — Final Review

Deliverable:

A multilingual MVP landing page ready for demonstration and early user feedback.
