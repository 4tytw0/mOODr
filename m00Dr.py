import customtkinter as ctk

Note_Dict = ['C', 'C#', 'D', 'D#', 'E', 'F',
             'F#', 'G', 'G#', 'A', 'A#', 'B']

Modes = ['Major', 'Major 7', 'Minor', 'Minor 7', 'Byzintine', 'Byzintine7', 'snhtri', 'snhtri7']

progression_conversions = {1: 'I',
                           2: 'II',
                           3: 'III',
                           4: 'IV',
                           5: 'V',
                           6: 'VI',
                           7: 'VII'}

"""Mode Dictionaries"""

major_intervals = {"I": 0,
                   "ii": 2,
                   "iii": 2,
                   "IV": 1,
                   "V": 2,
                   "vi": 2,
                   "vii°": 2,
                   "2I": 1
                   }  # Option,Shift,8 = °
minor_intervals = {"i": 0,
                   "ii°": 2,  # Option,Shift,8 = °
                   "III": 1,
                   "iv": 2,
                   "v": 2,
                   "VI": 1,
                   "VII": 2,
                   "2i": 2
                   }
byzintine_intervals = {
                    "I": 0,
                    "ii": 2,
                    "III": 2,
                    "IV": 1,
                    "V": 1,
                    "vi": 2,
                    "VII°": 2,
                    "2I": 2
                    }



class selectFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.keySelect = ctk.CTkComboBox(master,values=Note_Dict)

        self.keySelect.grid(row=0,column=0)

class funcFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.playButton = ctk.CTkButton(master, text="Play")
        self.stopButton = ctk.CTkButton(master, text="Stop")
        self.playButton.grid(row=1,column=0,pady=20)
        self.stopButton.grid(row=1,column=1, pady=20)

class chordFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.firstChordBtn = ctk.CTkButton(master, text="I")
        self.secondChordBtn = ctk.CTkButton(master, text="II")
        self.thirdChordBtn = ctk.CTkButton(master, text="III")
        self.fourthChordBtn = ctk.CTkButton(master, text="IV")
        self.fifthChordBtn = ctk.CTkButton(master, text="V")
        self.sixthChordBtn = ctk.CTkButton(master, text="VI")
        self.seventhChordBtn = ctk.CTkButton(master, text="VII")

        self.firstChordBtn.grid(row=2,column=0)
        self.secondChordBtn.grid(row=2,column=1)
        self.thirdChordBtn.grid(row=2,column=2)
        self.fourthChordBtn.grid(row=2,column=3)
        self.fifthChordBtn.grid(row=2,column=4)
        self.sixthChordBtn.grid(row=2,column=5)
        self.seventhChordBtn.grid(row=2,column=6)

class m00Dr(ctk.CTk):
    def __init__(self):
        super().__init__()
        #self.master = master
        self.wm_title('m00Dr')

        self.select_frame = selectFrame(master=self)

        self.func_frame = funcFrame(master=self)
        #self.func_frame.grid(row=2,column=0)

        self.chord_frame = chordFrame(master=self)
        #self.chord_frame.grid(row=2, column=0)

if __name__ == '__main__':
    app = m00Dr()
    #app = ctk.CTk()
    #gui = m00Dr()
    app.mainloop()