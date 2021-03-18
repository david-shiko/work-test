import asyncio
import aiofiles
from aiohttp import ClientSession as aiohttp_ClientSession
from bs4 import BeautifulSoup
from pathlib import Path as pathlib_Path
from json import dumps as json_dumps
from loguru import logger
from sys import platform as sys_platform

# # # HARDCODED # # #
MIN_YEAR = 2018
MAX_YEAR = 2020


def is_key_in_dict(dict_key: str, array: list):
    for i, dict_ in enumerate(array):
        if dict_['form_number'] == dict_key:
            return i


async def load_page(url: str, session, ):
    async with session.get(url=url, ) as resp:
        if 200 <= resp.status <= 299:
            return await resp.read()
        else:
            logger.error(f'Can not access a page, returned status code: {resp.status_code}')


async def parse_page_forms_json(rows: list) -> list[str]:  # Every page contain set of forms
    page_forms = []
    for row in rows:
        try:
            form_number = row.findChild(['LeftCellSpacer', 'a']).string.split(' (', 1)[0]  # split to remove non eng ver
            current_year = int(row.find(name='td', attrs={'class': 'EndCellSpacer'}).string.strip())
            index = is_key_in_dict(dict_key=form_number, array=page_forms)
            if index is None:
                page_forms.append({
                    'form_number': form_number,  # Title in reality
                    'form_title': row.find(name='td', attrs={'class': 'MiddleCellSpacer'}).string.strip(),
                    'min_year': current_year,
                    'max_year': current_year,
                })
            else:  # If exists - modify form
                if page_forms[index]['min_year'] > current_year:
                    page_forms[index]['min_year'] = current_year
                elif page_forms[index]['max_year'] < current_year:
                    page_forms[index]['max_year'] = current_year
        except Exception as e:
            logger.error(f'Error: {e}')
    return [json_dumps(page_form) for page_form in page_forms]  # What to do whit this data?


async def save_page_form_pdf(rows, session):
    for row in rows:
        try:
            current_year = int(row.find(name='td', attrs={'class': 'EndCellSpacer'}).string.strip())
            form_number_elem = row.findChild(['LeftCellSpacer', 'a'])  # link and name
            form_number = form_number_elem.string.split(' (', 1)[0]  # split to remove non eng ver
            if MIN_YEAR <= current_year <= MAX_YEAR:
                resp = await load_page(url=form_number_elem.attrs['href'], session=session)
                # See https://docs.python.org/3/library/pathlib.html#pathlib.Path.mkdir
                pathlib_Path(form_number).mkdir(parents=True, exist_ok=True)  # exist_ok - skip FileExistsError
                filename = f"{form_number}/{form_number}_{current_year}.pdf"
                async with aiofiles.open(file=filename, mode='wb') as f:
                    await f.write(resp)
        except Exception as e:
            logger.error(f'Can not save file, error: {e}')


async def main():
    async with aiohttp_ClientSession() as session:
        tasks = []
        pagination = 0
        while 1:
            url = (f'https://apps.irs.gov/app/picklist/list/priorFormPublication.html?'
                   f'indexOfFirstRow={pagination}&'
                   f'sortColumn=sortOrder&'
                   f'value=&criteria=&'
                   f'resultsPerPage=200&'
                   f'isDescending=false')
            page = await load_page(url=url, session=session)
            soup = BeautifulSoup(page, 'html.parser')
            table = soup.find(name='table', attrs={'class': 'picklist-dataTable'})  # Target
            rows = table.find_all(name='tr')[1:]  # [1:] - Just wrong HTML
            if rows:
                tasks.append(await parse_page_forms_json(rows=rows))  # Task 1
                tasks.append(await save_page_form_pdf(rows=rows, session=session))  # Task 2
                pagination += 200
            else:  # Stop pagination
                break
        await asyncio.gather(*tasks)


if __name__ == '__main__':
    # See https://github.com/encode/httpx/issues/914#issuecomment-622586610 (exit code is 0, but error is exists)
    if sys_platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logger.add('errors.txt', level='ERROR', rotation="30 days", backtrace=True)
    asyncio.run(main())


"""
README.txt

python version: > 3.9

Логика кода:
В цикле загружаются страницы (переход по старницам производится с помощью URL параметра, который изменяется в цикле).
Когда старница загружена - из нее извлекается таблица. Если таблицы нет - значит достигнута последняя страница.
Из таблицы извлекаются записи, по ним составляется json и из них извлекаются URL для скачивания PDF.
"""