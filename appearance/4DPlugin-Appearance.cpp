/* --------------------------------------------------------------------------------
 #
 #  4DPlugin-Appearance.cpp
 #	source generated by 4D Plugin Wizard
 #	Project : Appearance
 #	author : miyako
 #	2022/04/29
 #  
 # --------------------------------------------------------------------------------*/

#include "4DPlugin-Appearance.h"

#pragma mark -

static void Callback(CFNotificationCenterRef center,
                     void *observer,
                     CFStringRef name,
                     const void *object,
                     CFDictionaryRef userInfo);

static void listener_start()
{
    CFNotificationCenterRef distributedCenter = CFNotificationCenterGetDistributedCenter();
    CFNotificationSuspensionBehavior behavior = CFNotificationSuspensionBehaviorDeliverImmediately;
    CFNotificationCenterAddObserver(distributedCenter,
                                    NULL,
                                    Callback,
                                    CFSTR("AppleInterfaceThemeChangedNotification"),
                                    NULL,
                                    behavior);
}

static void listener_end()
{
    /* do this in main process */
    CFNotificationCenterRef distributedCenter = CFNotificationCenterGetDistributedCenter();
    CFNotificationCenterRemoveObserver(distributedCenter, NULL, CFSTR("AppleInterfaceThemeChangedNotification"), NULL);
}

#define MAX_PROCESS_NAME 256

typedef std::basic_string<PA_Unichar> CUTF16String;

static bool IsProcessOnExit()
{
    std::vector<PA_Unichar> name(MAX_PROCESS_NAME);

    PA_long32 state, time;
    
    PA_GetProcessInfo(PA_GetCurrentProcessNumber(), (PA_Unichar *)&name[0], &state, &time);
    
    CUTF16String procName((PA_Unichar *)&name[0]);
    CUTF16String exitProcName((PA_Unichar *)"$\0x\0x\0\0\0");
    return (!procName.compare(exitProcName));
}

#define CALLBACK_IN_NEW_PROCESS 0
#define CALLBACK_SLEEP_TIME 59

std::mutex globalMutex; /* for INSERT_RECORDS,UPDATE_RECORDS,DELETE_RECORDS */
std::mutex globalMutex1;/* for METHOD_PROCESS_ID */
std::mutex globalMutex2;/* for LISTENER_METHOD */
std::mutex globalMutex3;/* PROCESS_SHOULD_TERMINATE */
std::mutex globalMutex4;/* PROCESS_SHOULD_RESUME */

namespace appearance
{
    //constants
    process_name_t MONITOR_PROCESS_NAME = (PA_Unichar *)"$\0A\0P\0P\0E\0A\0R\0A\0N\0C\0E\0\0\0";
    process_stack_size_t MONITOR_PROCESS_STACK_SIZE = 0;

    //context management
    std::vector<appearance_t> APPEARANCE;

    CUTF16String LISTENER_METHOD;

    //callback management
    process_number_t METHOD_PROCESS_ID = 0;
    bool PROCESS_SHOULD_TERMINATE = false;
    bool PROCESS_SHOULD_RESUME = false;
}

static void OnStartup()
{
    PA_RunInMainProcess((PA_RunInMainProcessProcPtr)listener_start, NULL);

    listenerLoopStart();
}

static void OnCloseProcess()
{
    if(IsProcessOnExit())
    {
        listenerLoopFinish();
    }
}

void PluginMain(PA_long32 selector, PA_PluginParameters params) {
    
	try
	{
        switch(selector)
        {
            case kInitPlugin :
            case kServerInitPlugin :
                OnStartup();
                break;
                
            case kCloseProcess :
                OnCloseProcess();
                break;
                
			// --- Appearance
            
			case 1 :
                Get_system_color_scheme(params);
				break;
                
            case 2 :
                ON_APPEARANCE_CHANGE_CALL(params);
                break;

        }

	}
	catch(...)
	{

	}
}

#pragma mark -

static void get_application_appearance(appearance_t *scheme) {
    
    appearance_t appearance = appearance_light;
    
    if (@available(macOS 10.14, *)) {
        NSAppearance *effectiveAppearance = [[NSApplication sharedApplication]effectiveAppearance];
        if([@[NSAppearanceNameDarkAqua,
              NSAppearanceNameVibrantDark,
              NSAppearanceNameAccessibilityHighContrastDarkAqua,
              NSAppearanceNameAccessibilityHighContrastVibrantDark
            ]indexOfObjectIdenticalTo:effectiveAppearance.name]!= NSNotFound) {
            appearance = appearance_dark;
        }
    }
    
    *scheme = appearance;
}

static void ON_APPEARANCE_CHANGE_CALL(PA_PluginParameters params) {
    
    PA_Unistring *arg1 = PA_GetStringParameter(params, 1);
    
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex2);
        
        if(arg1->fLength) {
            appearance::LISTENER_METHOD = arg1->fString;
        }else{
            appearance::LISTENER_METHOD = (PA_Unichar *)"\0\0";
        }
 
    }
    
}

static appearance_t get_system_appearance() {
    
    appearance_t appearance = appearance_light;
    
    NSUserDefaults *userDefaults = [NSUserDefaults standardUserDefaults];

    if (@available(macOS 10.15, *)) {
        if([[userDefaults objectForKey:@"AppleInterfaceStyleSwitchesAutomatically"] boolValue]) {
            appearance = appearance_auto;
        }
    }
    
    if(appearance != appearance_auto) {
        if (@available(macOS 10.14, *)) {
            if([userDefaults objectForKey:@"AppleInterfaceStyle"] == nil) {
                appearance = appearance_light;
            }else{
                appearance = appearance_dark;
            }
        }
    }
    
    return appearance;
}

static void Get_system_color_scheme(PA_PluginParameters params) {
    
    PA_Unichar DARK[]  = { 'd', 'a', 'r', 'k', 0 };
    PA_Unichar LIGHT[] = { 'l', 'i', 'g', 'h', 't', 0 };
    PA_Unichar AUTO[]  = { 'a', 'u', 't', 'o', 0 };
    
    appearance_t appearance = get_system_appearance();
    
    switch (appearance) {
        case appearance_dark:
            PA_ReturnString(params, DARK);
            break;
        case appearance_auto:
            PA_ReturnString(params, AUTO);
            break;
        case appearance_light:
        default:
            PA_ReturnString(params, LIGHT);
            break;
    }

}

static void generateUuid(CUTF16String &uuid) {
    
    NSString *u = [[[NSUUID UUID]UUIDString]stringByReplacingOccurrencesOfString:@"-" withString:@""];
    uint32_t len = [u length];
    uint32_t size = (len * sizeof(PA_Unichar)) + sizeof(PA_Unichar);
    std::vector<uint8_t> buf(size);
    
    if([u getCString:(char *)&buf[0] maxLength:size encoding:NSUnicodeStringEncoding]){
        uuid = CUTF16String((const PA_Unichar *)&buf[0], len);
    }else{
        uuid = (PA_Unichar *)"\0\0";
    }

}

static void Callback(CFNotificationCenterRef center,
                     void *observer,
                     CFStringRef name,
                     const void *object,
                     CFDictionaryRef userInfo)
{
    NSDictionary *_userInfo = (NSDictionary *)userInfo;
        
    appearance_t appearance = (get_system_appearance() == appearance_dark) ? appearance_light : appearance_dark;
    
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex);
        
        appearance::APPEARANCE.push_back(appearance);
    }
    
    listenerLoopExecute();
}

void listenerLoop()
{
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex3);
        
        appearance::PROCESS_SHOULD_TERMINATE = false;
    }
    
    /* Current process returns 0 for PA_NewProcess */
    PA_long32 currentProcessNumber = PA_GetCurrentProcessNumber();
    
    while(!PA_IsProcessDying())
    {
        PA_YieldAbsolute();
        
        bool PROCESS_SHOULD_RESUME;
        bool PROCESS_SHOULD_TERMINATE;
        
        if(1)
        {
            PROCESS_SHOULD_RESUME = appearance::PROCESS_SHOULD_RESUME;
            PROCESS_SHOULD_TERMINATE = appearance::PROCESS_SHOULD_TERMINATE;
        }
        
        if(PROCESS_SHOULD_RESUME)
        {
            size_t appearances;
            
            if(1)
            {
                std::lock_guard<std::mutex> lock(globalMutex);
                
                appearances = appearance::APPEARANCE.size();
            }
            
            while(appearances)
            {
                PA_YieldAbsolute();
                
                if(CALLBACK_IN_NEW_PROCESS)
                {
                    CUTF16String processName;
                    generateUuid(processName);
                    PA_NewProcess((void *)listenerLoopExecuteMethod,
                                  appearance::MONITOR_PROCESS_STACK_SIZE,
                                  (PA_Unichar *)processName.c_str());
                }else
                {
                     listenerLoopExecuteMethod();
                }
                
                if(PROCESS_SHOULD_TERMINATE)
                    break;
                
                if(1)
                {
                    std::lock_guard<std::mutex> lock(globalMutex);
                    
                    appearances = appearance::APPEARANCE.size();
                    PROCESS_SHOULD_TERMINATE = appearance::PROCESS_SHOULD_TERMINATE;
                }
            }
            
            if(1)
            {
                std::lock_guard<std::mutex> lock(globalMutex4);
                
                appearance::PROCESS_SHOULD_RESUME = false;
            }
            
        }else
        {
            PA_PutProcessToSleep(currentProcessNumber, CALLBACK_SLEEP_TIME);
        }
        
        if(1)
        {
            PROCESS_SHOULD_TERMINATE = appearance::PROCESS_SHOULD_TERMINATE;
        }
        
        if(PROCESS_SHOULD_TERMINATE)
            break;
    }
    
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex);
        
        appearance::APPEARANCE.clear();
    }
    
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex2);
        
        appearance::LISTENER_METHOD = (PA_Unichar *)"\0\0";
    }

    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex1);
        
        appearance::METHOD_PROCESS_ID = 0;
    }
    
    PA_RunInMainProcess((PA_RunInMainProcessProcPtr)listener_end, NULL);
    
    PA_KillProcess();
}

void listenerLoopStart()
{
    if(!appearance::METHOD_PROCESS_ID)
    {
        std::lock_guard<std::mutex> lock(globalMutex1);
        
        appearance::METHOD_PROCESS_ID = PA_NewProcess((void *)listenerLoop,
                                                      appearance::MONITOR_PROCESS_STACK_SIZE,
                                                      appearance::MONITOR_PROCESS_NAME);
    }
}

void listenerLoopFinish()
{
    if(appearance::METHOD_PROCESS_ID)
    {
        if(1)
        {
            std::lock_guard<std::mutex> lock(globalMutex3);
            
            appearance::PROCESS_SHOULD_TERMINATE = true;
        }
        
        PA_YieldAbsolute();
        
        if(1)
        {
            std::lock_guard<std::mutex> lock(globalMutex4);

            appearance::PROCESS_SHOULD_RESUME = true;
        }
    }
}

void listenerLoopExecute()
{
    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex3);
        
        appearance::PROCESS_SHOULD_TERMINATE = false;
    }

    if(1)
    {
        std::lock_guard<std::mutex> lock(globalMutex4);
        
        appearance::PROCESS_SHOULD_RESUME = true;
    }

}

void listenerLoopExecuteMethod()
{
    appearance_t appearance;
    
    if(1)
    {
        //for reference; over-ride with the effective scheme
        
        std::lock_guard<std::mutex> lock(globalMutex);
        
        std::vector<appearance_t>::iterator it;
        
        it = appearance::APPEARANCE.begin();

        appearance = *it;
        
        appearance::APPEARANCE.erase(it);
    }
    
    PA_Unichar DARK[]  = { 'd', 'a', 'r', 'k', 0 };
    PA_Unichar LIGHT[] = { 'l', 'i', 'g', 'h', 't', 0 };
    
    PA_RunInMainProcess((PA_RunInMainProcessProcPtr)get_application_appearance, &appearance);

    method_id_t methodId = PA_GetMethodID((PA_Unichar *)appearance::LISTENER_METHOD.c_str());
    
    if(methodId)
    {
        PA_Variable    params[1];
        params[0] = PA_CreateVariable(eVK_Unistring);
        
        PA_Unistring arg1;
        
        switch (appearance) {
            case appearance_dark:
                arg1 = PA_CreateUnistring(DARK);
                break;
            case appearance_light:
                arg1 = PA_CreateUnistring(LIGHT);
            default:
                break;
        }
    
        PA_SetStringVariable(&params[0], &arg1);
                
        PA_ExecuteMethodByID(methodId, params, 1);
        
        PA_ClearVariable(&params[0]);

    }else if(appearance::LISTENER_METHOD.length() != 0)
    {
        PA_Variable    params[2];
        params[0] = PA_CreateVariable(eVK_Unistring);
        params[1] = PA_CreateVariable(eVK_Unistring);
        
        PA_Unistring method = PA_CreateUnistring((PA_Unichar *)appearance::LISTENER_METHOD.c_str());
        PA_SetStringVariable(&params[0], &method);
           
        PA_Unistring arg1;
        
        switch (appearance) {
            case appearance_dark:
                arg1 = PA_CreateUnistring(DARK);
                break;
            case appearance_light:
                arg1 = PA_CreateUnistring(LIGHT);
            default:
                break;
        }
        
        PA_SetStringVariable(&params[1], &arg1);

        PA_ExecuteCommandByID(1007 /* execute method */, params, 2);
        
        PA_ClearVariable(&params[0]);
        PA_ClearVariable(&params[1]);
    }
}