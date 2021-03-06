#!/usr/bin/python3

#
# fetch_articles.py
#
# news-fetch - Retrieve articles from the last month via Crom
# Copyright (c) 2021 SCP Wiki Technical Team
#
# news-fetch is available free of charge under the terms of the MIT
# License. You are free to redistribute and/or modify it under those
# terms. It is distributed in the hopes that it will be useful, but
# WITHOUT ANY WARRANTY. See the LICENSE file for more details.
#

import asyncio
import csv
import json
import sys
from dateutil.relativedelta import relativedelta
from dateutil.parser import isoparse

import aiohttp
import pytz

class CromError(RuntimeError):
    def __init__(self, errors):
        if len(errors) == 0:
            error_message = None
        elif len(errors) == 1:
            error_message = errors[0]['message']
        else:
            error_message = '\n'.join(error['message'] for error in errors)

        super().__init__(error_message)
        self.errors = errors

CROM_ENDPOINT = "https://api.crom.avn.sh/"
CROM_QUERY_TEMPLATE = """
{
  pages(
    sort: { order: ASC, key: CREATED_AT },
    filter: {
      wikidotInfo: { createdAt: { gte: "%(created_at)s" } },
      anyBaseUrl: [ "http://scp-wiki.wikidot.com" ]
    },
    first: 100,
    after: %(cursor)s
  ) {
    edges {
      node {
        url,
        wikidotInfo {
          createdAt,
          category,
          tags,
          rating,
          voteCount,
          revisionCount,
        },
        attributions {
            type,
            user { name },
            isCurrent,
        },
      }
    },
    pageInfo {
      hasPreviousPage,
      hasNextPage,
      endCursor,
    }
  }
}
"""

CROM_HEADERS = {
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def convert_edge_to_page(edge):
    node = edge['node']
    wikidot_info = node['wikidotInfo']
    attributions = node['attributions']
    rating = wikidot_info['rating']
    vote_count = wikidot_info['voteCount']
    downvote_count = (vote_count - rating) // 2

    authors = [
        attribution['user']['name']
        for attribution in attributions
        if attribution['isCurrent'] and attribution['type'] in ('AUTHOR', 'MAINTAINER', 'SUBMITTER')
    ]

    return {
        'url': node['url'],
        'category': wikidot_info['category'],
        'rating': rating,
        'vote_count': vote_count,
        'upvote_count': rating + downvote_count,
        'downvote_count': downvote_count,
        'created_at': wikidot_info['createdAt'],
        'authors': authors,
        'tags': wikidot_info['tags'],
        'revisions': wikidot_info['revisionCount'],
    }


def convert_pages_to_rows(pages):
    keys = [
        'url',
        'category',
        'rating',
        'vote_count',
        'upvote_count',
        'downvote_count',
        'created_at',
        'authors',
        'tags',
        'revisions',
    ]

    def convert(field):
        if isinstance(field, list):
            return ','.join(field)

        return field

    # First return the schema
    yield keys

    # Now return rows for each page
    for page in pages:
        yield [convert(page[key]) for key in keys]


def get_date_span(iso_date):
    start_date = isoparse(iso_date).replace(tzinfo=pytz.UTC)
    end_date = start_date + relativedelta(months=+1)

    return start_date, end_date


async def query_all(start_date, end_date):
    pages = []
    cursor = None
    has_next_page = True

    async with aiohttp.ClientSession() as session:
        while has_next_page:
            json_body = await query_one(session, start_date, cursor)

            edges = json_body['pages']['edges']
            page_info = json_body['pages']['pageInfo']
            has_next_page = page_info['hasNextPage']
            cursor = page_info['endCursor']

            for edge in edges:
                page = convert_edge_to_page(edge)
                page_created_at = isoparse(page['created_at'])
                if page_created_at > end_date:
                    has_next_page = False
                    break

                pages.append(page)

    return pages


async def query_one(session, created_at, cursor):
    print(f"+ Fetching pages... (cursor {cursor})")

    if cursor is None:
        cursor = 'null'
    else:
        cursor = f'"{cursor}"'

    query = CROM_QUERY_TEMPLATE % { 'created_at': created_at, 'cursor': cursor }
    payload = json.dumps({ 'query': query }).encode("utf-8")

    async with session.post(CROM_ENDPOINT, data=payload, headers=CROM_HEADERS) as r:
        json_body = await r.json()

        if 'errors' in json_body:
            raise CromError(json_body['errors'])

        return json_body['data']


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <year>-<month>-<day>")
        sys.exit(1)

    start_date_iso = sys.argv[1]
    start_date, end_date = get_date_span(start_date_iso)

    print(f"Retrieving results for the month starting on {start_date_iso}")
    pages = asyncio.run(query_all(start_date, end_date))
    print()

    file_prefix = f"pages-{start_date_iso}"

    print(f"Writing results to {file_prefix}.json")
    with open(f"output/{file_prefix}.json", 'w') as file:
        json.dump(pages, file)

    print(f"Writing results to {file_prefix}.csv")
    with open(f"output/{file_prefix}.csv", 'w') as file:
        rows = convert_pages_to_rows(pages)
        csv_writer = csv.writer(file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        csv_writer.writerows(rows)
