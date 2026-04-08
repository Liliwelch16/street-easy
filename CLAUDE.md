# Apartment Scout — Claude Code Task

## Daily Apartment Check (via MCP tools)

When asked to "check apartments", "run apartment scout", or "check for new listings", follow these steps:

### Step 1: Search Gmail

Search for emails with this subject:
```
subject:"For Rent: listings under $7,000 (base rent) in NYC and NJ with at least 2 bedrooms with at least 1.5 bathrooms with washer / dryer in unit, dishwasher, and private outdoor space"
```

Use `gmail_search_messages` to find unread emails matching this subject.

If no emails are found, report "No new StreetEasy alerts found" and stop.

### Step 2: Read and Parse the Email

Use `gmail_read_message` to read the full email body. Extract each apartment listing with these fields:
- **Address** (street address + unit number)
- **Neighborhood** (match to one of: Ditmas Park, Boreum Hill, Flatbush, Bed-Stuy, Stuyvesant Heights, Cobble Hill, Brooklyn Heights, Park Slope, Prospect Lefferts Gardens, Prospect Heights, Kensington, Crown Heights, East Flatbush, Windsor Terrace, Clinton Hill)
- **Beds** (number)
- **Baths** (number)
- **Sq. Ft.** (number, may be unknown/not listed — leave blank if so)
- **Cost** (monthly rent as a number, no dollar sign)
- **StreetEasy URL** (full link to the listing)

### Step 3: Check for Duplicates

Fetch the Notion database to see what listings already exist:
- Database URL: https://www.notion.so/2e3c589608ba80258aa2d583c468fd91
- Data source ID: `2e3c5896-08ba-804d-bd9f-000bb98f4a5a`

Compare StreetEasy URLs to avoid adding duplicates.

### Step 4: Add to Notion

For each NEW listing, create a page in the Notion database with:
- **Address**: the listing address (this is the title field)
- **Neighborhood**: matched neighborhood (select field)
- **Beds**: number of bedrooms
- **Baths**: number of bathrooms
- **Sq. Ft.**: square footage (omit if unknown)
- **Cost**: monthly rent
- **StreetEasy URL**: link to listing
- **Status**: always set to "Interested"
- **Virtual Tour**: leave empty (do not set)
- **Tour Date**: leave empty (do not set)
- **Score**: leave empty (do not set)

### Step 5: Archive the Email

Note: The Gmail MCP tools cannot archive emails. After processing, tell the user which email(s) were processed so they can archive manually.

## Notion Database Reference

- Database: 🐯 Apartment Hunting
- ID: `2e3c589608ba80258aa2d583c468fd91`
- Data source: `collection://2e3c5896-08ba-804d-bd9f-000bb98f4a5a`
