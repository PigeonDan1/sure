# -*- coding: utf-8 -*-
# Copyright (c) 2003, Taro Ogawa.  All Rights Reserved.
# Copyright (c) 2013, Savoir-faire Linux inc.  All Rights Reserved.

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301 USA

from __future__ import unicode_literals

from . import (lang_EN, lang_ZH_CN)

CONVERTER_CLASSES = {
    'en': lang_EN.Num2Word_EN(),
    'zh': lang_ZH_CN.Num2Word_ZH_CN(),
    'zh_CN': lang_ZH_CN.Num2Word_ZH_CN(),
    'zh_cn': lang_ZH_CN.Num2Word_ZH_CN(),
}

CONVERTES_TYPES = ['cardinal', 'ordinal', 'ordinal_num', 'year', 'currency']


def num2words(number, ordinal=False, lang='en', to='cardinal', **kwargs):
    # We try the full language first
    if lang not in CONVERTER_CLASSES:
        # ... and then try only the first 2 letters
        lang = lang[:2]
    if lang not in CONVERTER_CLASSES:
        # Fallback for case insensitivity if needed (though explicit keys handle it)
        lang_lower = lang.lower()
        if lang_lower in CONVERTER_CLASSES:
            lang = lang_lower
        else:
             raise NotImplementedError(f"Language {lang} not implemented")
            
    converter = CONVERTER_CLASSES[lang]

    if isinstance(number, str):
        number = converter.str_to_number(number)

    # backwards compatible
    if ordinal:
        to = 'ordinal'

    if to not in CONVERTES_TYPES:
        raise NotImplementedError()

    return getattr(converter, 'to_{}'.format(to))(number, **kwargs)
