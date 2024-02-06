import os
import re
import enum
import json
import logging
from datetime import datetime
from dataclasses import dataclass

import httpx
import numpy as np
from dotenv import load_dotenv
from more_itertools import flatten
from better_profanity import profanity
from selectolax.parser import HTMLParser


logger = logging.getLogger()
load_dotenv()
profanity.load_censor_words()


def remove_html(html: str) -> str:
    tree = HTMLParser(html)
    return tree.text().replace('\n', ' ')


def profanity_only(text: str) -> bool:
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'[^\w]', '', text)
    return len(text) == 0


class Periods(enum.Enum):
    YESTERDAY = 86400 
    ONE_WEEK = 604800
    TWO_WEEKS = 1209600
    ONE_MONTH = 2592000
    THREE_MONTHS = 7776000
    SIX_MONTHS = 15552000
    ONE_YEAR = 31104000


class Categories(enum.Enum):
    BIOENERGETICS = 5
    CASE_STUDIES = 6
    NOOSPHERE = 7
    JUNKYARD = 8
    META = 9


@dataclass
class Post:
    uid: int
    username: str
    is_banned: bool
    replies_to: str | None
    date: datetime
    content: str
    text: str | None = None

    def __post_init__(self) -> None:
        self.username = profanity.censor(self.username)
        if profanity_only(self.username):
            self.username = str(self.uid)
        if self.replies_to is not None:
            self.replies_to = profanity.censor(self.replies_to)
        self.text = profanity.censor(remove_html(self.content))
        self.content = profanity.censor(self.content)

    def to_tweet(self) -> str:
        date = self.date.strftime('%Y-%m-%d')
        tweet = f'[{date}] {self.username}'
        if self.replies_to is not None:
            tweet += f' to {self.replies_to}'
        tweet += ': '
        tweet += self.text
        return tweet


@dataclass 
class Topic:
    id: int
    title: str
    author: str
    author_uid: int
    date: datetime
    posts: list[Post]

    def __post_init__(self) -> None:
        self.title = profanity.censor(self.title)
        self.author = profanity.censor(self.author)
        if profanity_only(self.author):
            self.author = str(self.author_uid)


CATEGORY_BLACKLIST = [Categories.JUNKYARD]
PERIOD = Periods.ONE_MONTH
BASE_URL = 'https://bioenergetic.forum/api/'
BOT_USERNAME = 'brad'
IS_TWITTER_PREMIUM = False
TWITTER_CHAR_LIMIT = 4_000 if IS_TWITTER_PREMIUM else 280
HEADERS = {'Content-Type': 'application/json'}
CREDENTIALS = {
  'username': os.environ['FORUM_USERNAME'],
  'password': os.environ['FORUM_PASSWORD'],
}


def get_recent_mentions(page: int = None) -> dict:
    """
    Searches for the mentions of the bot newer than PERIOD.

    The relevant keys in the response are: 

    'posts'         : list of posts on the first page
    'matchCount'    : number of all posts
    'pageCount'     : number of pages
    'pagination'    : dict with pages' information
    'multiplePages' : boolean value indicating if result is paginated
    """
    url = BASE_URL + f'search?in=posts&term=%40{BOT_USERNAME}&matchWords=all&by=&categories=&searchChildren=false&hasTags=&replies=&repliesFilter=atleast&timeFilter=newer&timeRange={PERIOD.value}&sortBy=timestamp&sortDirection=desc&showAs=posts'
    if page is not None:
        url += f'&page={page}'
    try:

        with httpx.Client() as client:
            # create session
            client.post(
                BASE_URL+'v3/utilities/login', 
                headers=HEADERS,
                data=CREDENTIALS
            )
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        logger.info(f'Mentions {"" if page is None else "Page "+str(page)+" "}OK: {response.status_code} ({data["time"]}s).')
    except httpx.HTTPError as e:
        logger.error(f'Mentions {"" if page is None else "Page "+str(page)+" "}ERROR: {response.status_code}.')
        logger.critical(f'HTTP Exception for {e.request.url}: {e}.')
        raise e

    return data


def extract_topics(mentions: dict) -> list[str]:
    """
    Returns unique topic slugs that are not in CATEGORY_BLACKLIST 
    for all mentions provided.
    """
    if mentions['multiplePages']:
        topics = []
        for page in range(2, mentions['pageCount']+1):
            next_mentions = get_recent_mentions(page)
            topics += [post['topic'] for post in next_mentions['posts']]
    else:
        topics = [post['topic'] for post in mentions['posts']]

    topic_slugs = [topic['slug'] for topic in topics if topic['cid'] not in CATEGORY_BLACKLIST]
    return list(set(topic_slugs))


def postprocess_posts(posts: list[Post]) -> list[Post]:
    # remove posts added after taggin the bot
    tagging_posts = np.where([f'@{BOT_USERNAME}' in post.content for post in posts])[0]
    if len(tagging_posts) == 0:
        # temporary testing fix
        tagging_posts = [999]
    posts = [post for i, post in enumerate(posts) if i not in tagging_posts and i < max(tagging_posts)]
    # remove posts by banned users 
    posts = [post for post in posts if not post.is_banned]
    # remove posts containing only profanity
    posts = [post for post in posts if not profanity_only(post.text)]
    return posts


def compile_posts_into_topic(topic_slug: str) -> Topic:
    """
    Given topic slug, get all posts in the topic.
    """
    def get_page(url: str, page_num: int) -> dict:
        try:
            with httpx.Client() as client:
                # create session
                client.post(
                    BASE_URL+'v3/utilities/login', 
                    headers=HEADERS,
                    data=CREDENTIALS
                )
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
            logger.info(f'Topic {topic_slug} Page {page_num} OK: {response.status_code}.')
        except httpx.HTTPError as e:
            logger.error(f'Topic {topic_slug} Page {page_num} ERROR: {response.status_code}.')
            logger.critical(f'HTTP Exception for {e.request.url}: {e}.')
            raise e
        return data
    

    def extract_posts(page: dict) -> list[Post]:
        return [
            Post(
                uid=post['uid'],
                username=post['user']['username'],
                is_banned=post['user']['banned'],
                replies_to=post.get('parent', {}).get('username'),
                date=datetime.strptime(page['timestampISO'], '%Y-%m-%dT%H:%M:%S.%fZ'),
                content=post['content'],
            ) for post in page['posts']
        ]

    # get the first page
    url = BASE_URL+'topic/'+topic_slug
    page = get_page(url, 1) 
    page_count = page['pagination']['pageCount']
    id = page['tid']
    title = page['title']
    author = page['author']['username']
    author_uid = page['author']['uid']
    date = datetime.strptime(page['timestampISO'], '%Y-%m-%dT%H:%M:%S.%fZ')
    posts = extract_posts(page)

    for i in range(1, page_count+1):
        page = get_page(url+f'?page={i}', i)
        posts += extract_posts(page)

    posts = postprocess_posts(posts)

    return Topic(id, title, author, author_uid, date, posts)


def split_text_on_words(text: str, char_limit: int = TWITTER_CHAR_LIMIT) -> list[str]:
    if len(text) <= char_limit:
        return [text.strip(' \n')]
    
    lines = [line.strip(' \n') for line in re.findall(r'.{%d}' % char_limit, text)]

    ending_idx = text.rfind(lines[-1])+len(lines[-1])
    maybe_last_line = text[ending_idx:].strip(' \n')
    if maybe_last_line:
        lines.append(maybe_last_line)

    return lines


def topic_to_thread(topic: Topic) -> list[str]:
    date = topic.date.strftime('%Y-%m-%d')
    title = f'{topic.title} by {topic.author} ({date})'
    title_tweets = split_text_on_words(title)

    post_tweets = list(flatten([split_text_on_words(post.to_tweet()) for post in topic.posts]))
    return title_tweets + post_tweets


if __name__ == '__main__':
    mentions = get_recent_mentions()
    topic_slugs = extract_topics(mentions)
    threads = []
    for topic_slug in topic_slugs:
        topic = compile_posts_into_topic(topic_slug)
        thread = topic_to_thread(topic)
        threads.append(thread)
        print(json.dumps(thread, indent=2))
        break

