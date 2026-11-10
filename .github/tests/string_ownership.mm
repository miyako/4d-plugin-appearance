#include "4DPlugin-Appearance.cpp"
#include "support/4DPlugin-JSON.cpp"
#include "4D Plugin API/Classes/ARRAY_TEXT.cpp"
#include "4D Plugin API/Classes/C_TEXT.cpp"
#include "4D Plugin API/4DPluginAPI.c"

#include <cassert>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>

namespace {
std::set<PA_Unichar *> strings;
std::map<PA_Handle, size_t> handles;
bool allocatedEmpty;
bool directMethod;
bool propertyPresent;
appearance_t effective;
CUTF16String expectedMethod;
PA_Variable property = {};
int references = 0;
int objectToken;
int failures = 0;
int calls = 0;
bool callbackReturnsText = false;

PA_Unistring makeString(const PA_Unichar *text)
{
    size_t length = std::char_traits<PA_Unichar>::length(text);
    PA_Unistring value = {};
    value.fLength = static_cast<PA_long32>(length);
    if (length || allocatedEmpty) {
        value.fString = new PA_Unichar[length + 1];
        std::copy(text, text + length + 1, value.fString);
        PA_Unichar *pointer = value.fString;
        assert(strings.insert(pointer).second);
    }
    return value;
}

void dispose(PA_Unistring value)
{
    if (value.fString) {
        PA_Unichar *pointer = value.fString;
        assert(strings.erase(pointer) == 1);
        delete[] value.fString;
    }
}

CUTF16String contents(PA_Unistring value)
{
    PA_Unichar *pointer = value.fString;
    assert(value.fLength == 0 || strings.count(pointer));
    if (!value.fString) {
        assert(value.fLength == 0);
        return {};
    }
    assert(value.fString[value.fLength] == 0);
    return CUTF16String(value.fString, value.fLength);
}

void clear(PA_Variable *value)
{
    switch (value->fType) {
        case eVK_Unistring:
            dispose(value->uValue.fString);
            break;
        case eVK_ArrayUnicode:
            if (value->uValue.fArray.fData) {
                auto elements = reinterpret_cast<PA_Unistring *>(*value->uValue.fArray.fData);
                for (int i = 0; i <= value->uValue.fArray.fNbElements; ++i)
                    dispose(elements[i]);
                free(*value->uValue.fArray.fData);
                PA_Handle handle = value->uValue.fArray.fData;
                assert(handles.erase(handle) == 1);
                free(value->uValue.fArray.fData);
            }
            break;
        case eVK_Object:
            if (value->uValue.fObject) --references;
            break;
        case eVK_Collection:
            if (value->uValue.fCollection) --references;
            break;
    }
    assert(references >= 0);
    *value = {};
}

PA_Variable copy(PA_Variable value)
{
    if (value.fType == eVK_Unistring)
        value.uValue.fString = makeString(contents(value.uValue.fString).c_str());
    if ((value.fType == eVK_Object && value.uValue.fObject) ||
        (value.fType == eVK_Collection && value.uValue.fCollection))
        ++references;
    return value;
}

void checkCallback(PA_Variable *parameters, int count)
{
    assert(count == (directMethod ? 1 : 2));
    if (!directMethod)
        assert(contents(parameters[0].uValue.fString) == expectedMethod);
    CUTF16String expected = effective == appearance_dark
        ? CUTF16String((const PA_Unichar *)u"dark")
        : CUTF16String((const PA_Unichar *)u"light");
    // Churn unrelated allocations while the callback still borrows its input.
    for (int i = 0; i < 20; ++i) {
        PA_Unistring churn = PA_CreateUnistring((PA_Unichar *)u"churn");
        PA_DisposeUnistring(&churn);
    }
    assert(contents(parameters[count - 1].uValue.fString) == expected);
    ++calls;
}

void engine(short selector, EngineBlock *eb)
{
    eb->fError = eER_NoErr;
    switch (selector) {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            break;
        case EX_DISPOSE_UNISTRING:
            dispose(eb->fUniString1);
            break;
        case EX_SET_UNISTRING: {
            PA_Unistring replacement = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            dispose(eb->fUniString1);
            eb->fUniString1 = replacement;
            break;
        }
        case EX_VARIABLE_TO_STRING:
            eb->fUniString1 = reinterpret_cast<PA_Variable *>(eb->fHandle)->uValue.fString;
            break;
        case EX_CLEAR_VARIABLE:
            clear(static_cast<PA_Variable *>(eb->fPtr1));
            break;
        case EX_HANDLE_MANAGER: {
            PA_Handle h = eb->fHandle;
            switch (eb->fParam1) {
                case 1:
                    h = static_cast<PA_Handle>(malloc(sizeof(char *)));
                    *h = static_cast<char *>(calloc(1, eb->fParam2));
                    handles[h] = eb->fParam2;
                    eb->fHandle = h;
                    break;
                case 2:
                    assert(handles.erase(h) == 1);
                    free(*h);
                    free(h);
                    break;
                case 3: {
                    size_t old = handles.at(h);
                    *h = static_cast<char *>(realloc(*h, eb->fParam2));
                    if (static_cast<size_t>(eb->fParam2) > old)
                        memset(*h + old, 0, eb->fParam2 - old);
                    handles[h] = eb->fParam2;
                    break;
                }
                case 4: eb->fParam2 = handles.at(h); break;
                case 5: eb->fParam3 = reinterpret_cast<sLONG_PTR>(*h); break;
                case 6: break;
                case 7: eb->fParam2 = 0; break;
                case 8: break;
                default: assert(false);
            }
            break;
        }
        case EX_GET_METHOD_ID:
            eb->fParam1 = directMethod && !expectedMethod.empty() ? 42 : 0;
            break;
        case EX_RUN_IN_MAIN_PROCESS:
            *static_cast<appearance_t *>(eb->fPtr2) = effective;
            break;
        case EX_CALL_BY_PROCID: {
            auto params = reinterpret_cast<PA_Variable *>(*eb->fHandle);
            checkCallback(params + 1, eb->fParam2);
            if (callbackReturnsText) {
                PA_Unistring result = PA_CreateUnistring((PA_Unichar *)u"returned");
                PA_SetStringVariable(params, &result);
            }
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID: {
            auto params = static_cast<PA_Variable **>(eb->fPtr1);
            auto result = static_cast<PA_Variable *>(eb->fPtr2);
            if (eb->fParam1 == 1007) {
                PA_Variable values[] = {*params[0], *params[1]};
                checkCallback(values, eb->fParam2);
            } else if (eb->fParam1 == 1231) {
                PA_SetBooleanVariable(result, propertyPresent);
            } else {
                assert(false);
            }
            break;
        }
        case EX_SET_OBJ_VALUE:
            clear(&property);
            property = copy(*static_cast<PA_Variable *>(eb->fPtr2));
            propertyPresent = true;
            break;
        case EX_GET_OBJ_VALUE:
            *static_cast<PA_Variable *>(eb->fPtr2) = copy(property);
            break;
        default:
            std::cerr << "Unexpected SDK engine selector " << selector << "\n";
            abort();
    }
}

void balanced(const char *scenario)
{
    clear(&property);
    if (!strings.empty() || !handles.empty() || references) {
        std::cerr << scenario << " leaked " << strings.size()
                  << " strings, " << handles.size() << " handles, "
                  << references << " references; allocatedEmpty=" << allocatedEmpty << "\n";
        ++failures;
    }
    // Isolate independent baseline failures, without hiding them from the exit status.
    for (auto string : strings) delete[] string;
    strings.clear();
    assert(handles.empty());
    assert(references == 0);
}

void stringProperty(const PA_Unichar *text)
{
    clear(&property);
    PA_Unistring value = PA_CreateUnistring(const_cast<PA_Unichar *>(text));
    PA_SetStringVariable(&property, &value);
    propertyPresent = true;
}
}

int main()
{
    gCall4D = engine;
    auto object = reinterpret_cast<PA_ObjectRef>(&objectToken);
    const wchar_t *wide[] = {L"", L"ASCII", L"\u65e5\u672c", L"\U0001f600"};
    const char *utf8[] = {"", "ASCII", "\xe6\x97\xa5\xe6\x9c\xac", "\xf0\x9f\x98\x80"};
    const PA_Unichar *utf16[] = {
        (const PA_Unichar *)u"", (const PA_Unichar *)u"ASCII",
        (const PA_Unichar *)u"\u65e5\u672c", (const PA_Unichar *)u"\U0001f600"
    };
    for (bool emptyModel : {false, true}) {
        allocatedEmpty = emptyModel;
        for (bool direct : {false, true}) {
            directMethod = direct;
            for (auto mode : {appearance_light, appearance_dark, appearance_auto}) {
                effective = mode;
                for (int i = 0; i < 4; ++i) {
                    expectedMethod = utf16[i];
                    appearance::LISTENER_METHOD = expectedMethod;
                    for (int repeat = 0; repeat < 3; ++repeat) {
                        callbackReturnsText = repeat == 2;
                        appearance::APPEARANCE.push_back(mode);
                        int before = calls;
                        listenerLoopExecuteMethod();
                        assert(calls == before + (i != 0));
                        balanced("callback");
                    }
                }
            }
        }
        for (int i = 0; i < 4; ++i) {
            CUTF16String value = utf16[i];
            PA_Variable text = PA_CreateVariable(eVK_Unistring);
            unsigned char *textParameters[] = {
                reinterpret_cast<unsigned char *>(&text.uValue.fString)
            };
            {
                C_TEXT writer;
                writer.setUTF16String(&value);
                writer.toParamAtIndex(textParameters, 1);
            }
            C_TEXT reader;
            reader.fromParamAtIndex(textParameters, 1);
            CUTF16String copied;
            reader.copyUTF16String(&copied);
            assert(copied == value);
            PA_Unistring *destination = &text.uValue.fString;
            PluginBlock returnParameters = {};
            returnParameters.fResult = &destination;
            PA_ReturnString(&returnParameters, const_cast<PA_Unichar *>(utf16[i]));
            assert(contents(text.uValue.fString) == value);
            PA_ClearVariable(&text);
            balanced("text wrapper and return content setter");
            for (int variant = 0; variant < 4; ++variant) {
                switch (variant) {
                    case 0: ob_set_s(object, "key", utf8[i]); break;
                    case 1: ob_set_s(object, L"key", utf8[i]); break;
                    case 2: ob_set_a(object, L"key", wide[i]); break;
                    case 3: ob_set_a(object, L"key", &value); break;
                }
                assert(contents(property.uValue.fString) == value);
                balanced("JSON setter");
            }
            for (int getter = 0; getter < 5; ++getter) {
                stringProperty(utf16[i]);
                CUTF8String result;
                switch (getter) {
                    case 0:
                        assert(ob_get_a(object, L"key", &result));
                        assert(result == CUTF8String((const uint8_t *)utf8[i]));
                        break;
                    case 1: assert(!ob_get_b(object, L"key")); break;
                    case 2: assert(ob_get_n(object, L"key") == 0); break;
                    case 3: assert(!ob_get_c(object, L"key")); break;
                    case 4: assert(!ob_get_o(object, L"key")); break;
                }
                balanced("JSON getter");
            }
        }
        for (auto type : {eVK_Object, eVK_Collection}) {
            property.fType = type;
            if (type == eVK_Object) property.uValue.fObject = object;
            else property.uValue.fCollection = reinterpret_cast<PA_CollectionRef>(&objectToken);
            references = 1;
            if (type == eVK_Object) {
                PA_ObjectRef result = ob_get_o(object, L"key");
                assert(result == object && references == 2);
                PA_DisposeObject(result);
            } else {
                PA_CollectionRef result = ob_get_c(object, L"key");
                assert(result == property.uValue.fCollection && references == 2);
                PA_DisposeCollection(result);
            }
            balanced("JSON reference handoff");
        }
        PA_SetBooleanVariable(&property, true);
        assert(ob_get_b(object, L"key"));
        balanced("boolean property");
        PA_SetRealVariable(&property, 12.5);
        assert(ob_get_n(object, L"key") == 12.5);
        balanced("number property");
        propertyPresent = false;
        CUTF8String result;
        assert(!ob_get_a(object, L"missing", &result));
        assert(!ob_get_b(object, L"missing"));
        assert(ob_get_n(object, L"missing") == 0);
        assert(!ob_get_c(object, L"missing"));
        assert(!ob_get_o(object, L"missing"));
        balanced("missing properties");

        PA_Variable array = PA_CreateVariable(eVK_ArrayUnicode);
        unsigned char *parameters[] = {reinterpret_cast<unsigned char *>(&array)};
        ARRAY_TEXT wrapper;
        for (int i = 0; i < 4; ++i) wrapper.appendUTF16String(utf16[i]);
        for (int repeat = 0; repeat < 3; ++repeat) {
            wrapper.toParamAtIndex(parameters, 1);
            ARRAY_TEXT reader;
            reader.fromParamAtIndex(parameters, 1);
            assert(reader.getSize() == 4);
            for (int i = 0; i < 4; ++i) {
                CUTF16String value;
                reader.copyUTF16StringAtIndex(&value, i);
                assert(value == utf16[i]);
            }
        }
        PA_Unistring rejected = PA_CreateUnistring((PA_Unichar *)u"caller-owned");
        PA_SetStringInArray(array, -1, &rejected);
        PA_SetStringInArray(array, 4, &rejected);
        PA_Variable scalar = PA_CreateVariable(eVK_Longint);
        PA_SetStringInArray(scalar, 0, &rejected);
        PA_Variable nullArray = {};
        nullArray.fType = eVK_ArrayUnicode;
        PA_SetStringInArray(nullArray, 0, &rejected);
        PA_SetStringInArray(array, 0, NULL);
        assert(contents(rejected) == (const PA_Unichar *)u"caller-owned");
        PA_DisposeUnistring(&rejected);
        PA_ResizeArray(&array, 0);
        PA_ClearVariable(&array);
        balanced("array replacement, rejection and clear");
    }
    std::cout << "ownership failures: " << failures << "\n";
    return failures ? 1 : 0;
}
