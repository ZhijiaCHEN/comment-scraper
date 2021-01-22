import sys, requests, json, os, argparse, random, time
from typing import ClassVar
from requests import HTTPError
from urllib.parse import urlparse
import functools
from bs4 import BeautifulSoup
from typing import Tuple
from requests.api import options
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException

def base_header():
    headers = \
    {
        "Accept-Language": "en-US,en;q=0.5",\
        "Accept-Encoding":"gzip, deflate, br",\
        "Connection":"close",\
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:84.0) Gecko/20100101 Firefox/84.0"
    }
    return headers

class Message():
    def error(self, msg):
        print('Error: \n', msg)
    
    def warn(self, msg):
        print('Warn: \n', msg)

    def info(self, msg):
        print('Info: \n', msg)

    def debug(self, msg):
        print('Debug: \n', msg)

class __SolutionSkeleton__(Message):
    def __init__(self) -> None:
        self.headers = base_header()
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "*/*"

        self.targetUrl = None

    def request_routine(self, url):
        # comment request routine, return the json object of comments
        self.targetUrl = url

class NewYorkTimes(__SolutionSkeleton__):
    def __init__(self) -> None:
        super().__init__()
        self.headers["Host"] = "www.nytimes.com"

    def request_routine(self, articleURL):
        super().request_routine(articleURL)

        self.headers["Referer"] = articleURL
        commentsURL = "https://www.nytimes.com/svc/community/V3/requestHandler?url={articleURL}&method=get&commentSequence=0&offset=0&includeReplies=true&sort=oldest&cmd=GetCommentsAll&limit=-1".format(articleURL=articleURL)

        response = requests.get(commentsURL, headers = self.headers)
        try:
            response.raise_for_status()
        except HTTPError as e:
            self.error("Failed to get comments for article: {}\n{}".format(articleURL, repr(e)))
            return
        
        comments = response.json()
        parentComments = comments['results']['comments']
        replyCnt = 0
        for cmt in parentComments:
            # by default, only 3 replies are returned
            if cmt["replyCount"] > 3:
                replies = self.__get_reply_comments__(articleURL, cmt['commentSequence'], 0, cmt["replyCount"])
                if replies:
                    cmt['replies'] = replies
                    replyCnt += (len(replies) - 3)
        comments['results']['totalCommentsReturned'] += replyCnt
        comments['results']['totalReplyCommentsReturned'] += replyCnt
        
        return comments
    
    def __get_reply_comments__(self, articleURL, commentSequence, offset, limit):
        commentsURL = "https://www.nytimes.com/svc/community/V3/requestHandler?url={articleURL}&method=get&commentSequence={commentSequence}&offset={offset}&limit={limit}&cmd=GetRepliesBySequence".format(articleURL=articleURL, commentSequence=commentSequence, offset=offset, limit=limit)

        response = requests.get(commentsURL, headers = self.headers)
        try:
            response.raise_for_status()
        except HTTPError as e:
            self.error("Failed to get reply comments for for comment {} of article: {}\n{}".format(commentSequence, articleURL, repr(e)))
            return None
        return response.json()['results']['comments'][0]['replies']

class CoralByGet(__SolutionSkeleton__):
    pass

class CoralByPost(__SolutionSkeleton__):
    def __init__(self, endpoint, batchSize = 500) -> None:
        super().__init__()
        self.LIMIT = batchSize
        self.API_ENDPOINT = endpoint

        parsedUrl = urlparse(endpoint)
        self.headers["Host"] = parsedUrl.netloc
        self.headers["Origin"] = "{}://{}".format(parsedUrl.scheme, parsedUrl.netloc)

    @staticmethod
    def __load_initial_query__():
        with open('coral-initial-query.txt', encoding='utf-8') as f:
            return f.read()
    
    @staticmethod
    def __load_more_query__():
        with open('coral-load-more-query.txt', encoding='utf-8') as f:
            return f.read()

    def __build_initial_request_payload__(self, url):
        query = self.__load_initial_query__().replace('__LIMIT__', str(self.LIMIT))
        payload = {
            "query": query,
            "variables": {
                "assetId": "",
                "assetUrl": url,
                "commentId": "",
                "hasComment": False,
                "excludeIgnored": False,
                "sortBy": "CREATED_AT",
                "sortOrder": "ASC"
            }
        }
        return json.dumps(payload)

    def __build_request_more_payload__(self, cursor, parentID, assetID):
        query = self.__load_more_query__().replace('__LIMIT__', str(self.LIMIT))
        payload = {
            "query": query,
            "variables": {
                "limit": self.LIMIT,
                "cursor": cursor,
                "parent_id": parentID,
                "asset_id": assetID,
                "sortOrder": "ASC",
                "sortBy": "CREATED_AT",
                "excludeIgnored": False
            }
        }
        return json.dumps(payload)

    def request_routine(self, articleURL):
        super().request_routine(articleURL)
        #self.headers["Referer"] = articleURL
        # parsedUrl = urlparse(articleURL)
        # articleURL = '{}://{}{}'.format(parsedUrl.scheme, parsedUrl.netloc, parsedUrl.path)
        # request initial comments
        payload = self.__build_initial_request_payload__(articleURL)
        response = requests.post(self.API_ENDPOINT, headers = self.headers, data = payload)
        try:
            response.raise_for_status()
            data = response.json()
        except HTTPError as e:
            self.error("Failed to get comments for article: {}\n{}".format(articleURL, repr(e)))
            return
    
        assetID = data['data']['asset']['id']
        comments = data['data']['asset']['comments']

        cnt = 0
        requestCnt = 1
        # dfs each comment and load all replies as needed.
        def load_replies(assetID, parentNode, parentID):
            nonlocal cnt, requestCnt
            hasNextPage = parentNode['hasNextPage']
            cursor = parentNode['endCursor']
            while hasNextPage:
                requestCnt += 1
                response = requests.post(self.API_ENDPOINT, headers = self.headers, data = self.__build_request_more_payload__(cursor, parentID, assetID))
                try:
                    response.raise_for_status()
                    replies = response.json()
                except HTTPError as e:
                    self.error("Failed to get part of comments for article: {}\n{}".format(articleURL, repr(e)))
                    break
                parentNode['nodes'] += replies['data']['comments']['nodes']
                hasNextPage = replies['data']['comments']['hasNextPage']
                cursor = replies['data']['comments']['endCursor']

            for cmt in parentNode['nodes']:
                cnt += 1
                if cmt['replyCount'] > 0 and 'replies' not in cmt:
                    cmt['replies'] = {
                            "nodes": [],
                            "hasNextPage": True,
                            "startCursor": None,
                            "endCursor": cmt['created_at'],
                            "__typename": "CommentConnection"
                        }
                if 'replies' in cmt:
                    load_replies(assetID, cmt['replies'], cmt['id'])

        load_replies(assetID, comments, None)
        self.info('{} comments loaded, {} requests made for article {}'.format(cnt, requestCnt,articleURL))
        return data

class WashingtonPost(CoralByPost):
    def __init__(self, batchSize=500) -> None:
        super().__init__("https://www.washingtonpost.com/talk/api/v1/graph/ql", batchSize=batchSize)

class SeattleTimes(CoralByPost):
    def __init__(self, batchSize=500) -> None:
        super().__init__("https://seattletimes.talk.coralproject.net/api/v1/graph/ql", batchSize=batchSize)

class TheIntercept(CoralByPost):
    def __init__(self, batchSize=500) -> None:
        super().__init__("https://talk.theintercept.com/api/v1/graph/ql", batchSize=batchSize)
    
    def request_routine(self, articleURL):
        headers = base_header()
        headers["Host"] = "theintercept.com"
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        headers['Accept-Encoding'] = 'identity'
        response = requests.get(articleURL, headers =headers)
        s = response.content.decode(encoding=response.encoding)
        idx = s.find('post_id')
        if idx == -1:
            self.error('Cannot find post id in {}.'.format(articleURL))
            return
        postID = []
        while not s[idx].isdigit():
            idx += 1
        while idx < len(s) and s[idx].isdigit():
            postID.append(s[idx])
            idx += 1
        postID = ''.join(postID)

        return super().request_routine("https://theintercept.com/?p={}".format(postID))

class DeseretNews(CoralByPost):
    def __init__(self, batchSize=500) -> None:
        super().__init__("https://deseretnews.talk.coralproject.net/api/v1/graph/ql", batchSize=batchSize)

class NUnl(CoralByPost):
    def __init__(self, batchSize=500) -> None:
        super().__init__("https://talk.nu.nl/api/v1/graph/ql", batchSize=batchSize)
    
    def request_routine(self, articleURL):
        try:
            postID = articleURL.split('/')[4]
        except Exception as e:
            self.error('Unrecognized patter for url from NU.nl.')
            return

        return super().request_routine("https://www.nu.nl/artikel/{}/redirect.html".format(postID))

class SpotIM(__SolutionSkeleton__):
    def __init__(self, maxDepth = 10, maxReply = 2) -> None:
        """Initiate a comment crawler for Spot.IM system.

        Args:
            maxDepth (int, optional): the max depth of replies to request. A root comment has depth 0, and a reply has depth 1 + the depth of its parent. Defaults to 10.
            maxReply (int, optional): the maximum number of replies to request for a reply message. By default, the Spot.IM system returns at most two replies under each reply message. A larger maxRely will significantly increase the number of requests to be sent. Use -1 to request all replies. Defaults to 2.
        """
        super().__init__()

        self.maxDepth = maxDepth
        self.maxReply = maxReply
        self.API_ENDPOINT = "https://api-2-0.spot.im/v1.0.0/conversation/read"
        self.AUTH_ENDPOINT = "https://api-2-0.spot.im/v1.0.0/authenticate"
        self.BATCH_SIZE = 200
        parsedUrl = urlparse(self.API_ENDPOINT)
        self.headers["Host"] = parsedUrl.netloc
        self.cookies = {}
        firefoxOption = webdriver.FirefoxOptions()
        firefoxOption.headless = True
        self.driver = webdriver.Firefox(options=firefoxOption)
        self.driver.set_page_load_timeout(15)
        self.driver.set_script_timeout(15)

    def __random_id__(self) -> str:
        """Randomly generate a 32 hex digit random id in the format of "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx".

        Returns:
            str: random id
        """
        id = ''.join([hex(random.randint(0, 15))[-1] for _ in range(32)])
        return '{}-{}-{}-{}-{}'.format(id[:8], id[8:12], id[12:16], id[16:20], id[20:])

    def __authenticate__(self)->str:
        """Request an access toke.
        # Args:
        #     postID (str): post ID of the target article page.
        #     spotID (str): spot ID of the target website.
        #     deviceID (str): device ID of the browser tab, can be generated randomly.

        Returns:
            str: access token
        """
        response = requests.post(self.AUTH_ENDPOINT, headers = self.headers)
        try:
            response.raise_for_status()
        except HTTPError as e:
            self.error("Failed to get access token from Spot.IM: \n{}".format(repr(e)))
            return None
        return response.headers['x-access-token']
    
    def __load_comments__(self)->dict:
        """Load comments of the target post.

        Returns:
            dict: json object of the received comments.
        """
        payload = {"sort_by":"oldest","offset":0,"count":self.BATCH_SIZE,"depth":self.maxDepth}
        data = requests.post(self.API_ENDPOINT, headers = self.headers, cookies = self.cookies, data=json.dumps(payload)).json()
        requestCnt = 1
        hasNxt = data['conversation']['has_next']
        offset = data['conversation']['offset']
        while hasNxt:
            time.sleep(random.random())
            payload['offset'] = offset
            dataNxt = requests.post(self.API_ENDPOINT, headers = self.headers, cookies = self.cookies, data=json.dumps(payload)).json()
            data['conversation']['comments'].extend(dataNxt['conversation']['comments'])
            hasNxt = dataNxt['conversation']['has_next']
            offset = dataNxt['conversation']['offset']
            requestCnt += 1

            self.debug('{} requests sent, {} comments received for target: {}.'.format(requestCnt, len(data['conversation']['comments']), self.targetUrl))

        cmtCnt = 0
        for cmt in data['conversation']['comments']:
            cmtCnt += 1
            if cmt['replies_count'] > 0 and cmt['depth'] < self.maxDepth:
                (childRequestCnt, childCmtCnt) = self.__load_replies__(cmt)
                requestCnt += childRequestCnt
                cmtCnt += childCmtCnt

        self.info('{} requests made, {} comments received for target: {}.'.format(requestCnt, cmtCnt, self.targetUrl))
        return data

    def __load_replies__(self, parentNode: dict) -> Tuple[int, int]:
        """Load replies of a parent message.

        Args:
            parentNode (dict): parent node.

        Returns:
            Tuple[int, int]: (# requests sent, # replies received)
        """
        requestCnt, cmtCnt = 0, 0
        payload = {"sort_by":"oldest","offset":0,"count":self.BATCH_SIZE,"depth":self.maxDepth}
        payload["parent_id"] = parentNode["id"]

        hasNxt = parentNode['has_next']
        offset = parentNode['offset']
        while hasNxt and len(parentNode['replies']) < self.maxReply:
            time.sleep(random.random())
            requestCnt += 1
            payload['offset'] = offset
            dataNxt = requests.post(self.API_ENDPOINT, headers = self.headers, cookies = self.cookies, data=json.dumps(payload)).json()
            parentNode['replies'].extend(dataNxt['conversation']['comments'])
            hasNxt = dataNxt['conversation']['has_next']
            offset = dataNxt['conversation']['offset']
            requestCnt += 1

            self.debug('{} requests sent, {} replies received for comment id {} in target: {}.'.format(requestCnt, len(parentNode['replies']), parentNode["id"], self.targetUrl))

        for cmt in parentNode['replies']:
            cmtCnt += 1
            if cmt['replies_count'] > 0 and cmt['depth'] < self.maxDepth:
                (childRequestCnt, childCmtCnt) = self.__load_replies__(cmt)
                requestCnt += childRequestCnt
                cmtCnt += childCmtCnt

        return (requestCnt, cmtCnt)

    def request_routine(self, articleURL: str) -> None:
        """Request comments from a url.

        Args:
            articleURL (str): target url.
        """
        super().request_routine(articleURL)
        try:
            self.driver.get(articleURL)
        except TimeoutException:
            pass
        
        try:
            # scroll down
            scrollCnt = 1
            lenOfPage = self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight;")
            match=False
            while not match and scrollCnt < 10:
                lastCount = lenOfPage
                time.sleep(1)
                lenOfPage = self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight;")
                if lastCount==lenOfPage:
                    match=True
                scrollCnt += 1

            e = self.driver.find_element_by_xpath('//*[@data-post-id and @data-spot-id]')
            spotID = e.get_attribute('data-spot-id')
            postID = e.get_attribute('data-post-id')
            deviceID = self.__random_id__()
            viewID = self.__random_id__()
        except NoSuchElementException:
            self.error('Cannot find spot ID and post ID in url: {}'.format(articleURL))
            return

        self.headers["x-post-id"] = postID
        self.headers["x-spot-id"] = spotID
        self.headers["x-spotim-device-uuid"] = deviceID

        token = self.__authenticate__()
        if token:
            self.headers["x-spotim-page-view-id"] = viewID
            self.cookies["device_uuid"] = deviceID
            self.cookies["access_token"] = token

            return self.__load_comments__()

SOLUTION_MAP = {'www.washingtonpost.com': WashingtonPost, 'www.seattletimes.com': SeattleTimes, 'www.nytimes.com': NewYorkTimes, 'theintercept.com': TheIntercept, 'www.deseret.com': DeseretNews, 'www.nu.nl': NUnl, 'Spot.IM': SpotIM}

class CommentScraper(Message):
    def __init__(self) -> None:
        pass

    def __is_spot_im__(self, url: str) -> bool:
        """Search if Spot.IM keywords exist in HTML source codes of the input url.

        Args:
            url (str): target page.

        Returns:
            bool: wether the target page has Spot.IM keywords or not.
        """
        headers = base_header()
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        headers['Accept-Encoding'] = 'identity'
        response = requests.get(url, headers = headers)
        try:
            response.raise_for_status()
        except HTTPError as e:
            self.error("Failed to request HTML source codes from {}: \n{}".format(url, repr(e)))
            return False

        source = response.content.decode(encoding=response.encoding)
        source = source.lower()
        return 'spotim' in source or 'spot-im' in source

    def __get_solution__(self, url, host):
        if host in SOLUTION_MAP:
            return SOLUTION_MAP[host]()
        elif self.__is_spot_im__(url):
            return SOLUTION_MAP['Spot.IM']()
        else:
            return None

    def __build_output__(self, parsedUrl, filepath, filename):
        if filename:
            if filepath:
                output = os.path.join(filepath, filename)
            else:
                output = os.path.join('.', filename)
        else:
            urlPath = parsedUrl.path.split('/')
            if urlPath[-1] == '':
                urlPath.pop()
            if urlPath[0] == '':
                del urlPath[0]

            filename = urlPath.pop().split('.')
            if len(filename) > 1:
                filename.pop()
            filename = '.'.join(filename) + '.json'
            if filepath:
                output = os.path.join(filepath, parsedUrl.netloc, *urlPath, filename)
            else:
                output = os.path.join('.', parsedUrl.netloc, *urlPath, filename)
        return output

    def load_comments(self, articleURL, filepath=None, filename=None):
        parsedUrl = urlparse(articleURL)
        articleURL = '{}://{}{}'.format(parsedUrl.scheme, parsedUrl.netloc, parsedUrl.path)
        output = self.__build_output__(parsedUrl, filepath, filename)

        sol = self.__get_solution__(articleURL, parsedUrl.netloc)
        if sol:
            comments = sol.request_routine(articleURL)
            if comments:
                if not os.path.exists(os.path.dirname(output)):
                    try:
                        os.makedirs(os.path.dirname(output))
                    except Exception as e:
                        self.error(repr(e))
                        return
                with open(output, 'w+', encoding='utf-8') as f:
                    json.dump(comments, f, ensure_ascii=False, indent=4)
        else:
            self.error('{} is not supported.'.format(parsedUrl.netloc))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Scrape comments from the input article URL.')
    parser.add_argument('-url', '--url', type=str, help='An article url from supported websites.')
    parser.add_argument('--filename', type=str, help='The output file name for the comments json file. If not given, the url path is used.')
    parser.add_argument('--filepath', type=str, help='The output file path for the comments json file, default to current directory.')
    parser.add_argument('-l', '--list', action='store_true', help='List supported websites.')
    
    args = parser.parse_args()
    if args.list:
        print('\n'.join(SOLUTION_MAP))
    if args.url:
        scraper = CommentScraper()
        scraper.load_comments(args.url, filepath=args.filepath, filename=args.filename)
    exit()
