document.addEventListener('DOMContentLoaded', () => {
    
    // ==========================================
    // 1. MENÚ MÓVIL (HAMBURGUESA)
    // ==========================================
    const mobileMenuBtn = document.getElementById('mobile-menu');
    const navMenu = document.querySelector('.nav-menu');

    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', () => {
            navMenu.classList.toggle('active');
            mobileMenuBtn.classList.toggle('is-active');
            
            if(navMenu.classList.contains('active')) {
                mobileMenuBtn.children[0].style.transform = "rotate(-45deg) translate(-5px, 6px)";
                mobileMenuBtn.children[1].style.opacity = "0";
                mobileMenuBtn.children[2].style.transform = "rotate(45deg) translate(-5px, -6px)";
            } else {
                mobileMenuBtn.children[0].style.transform = "none";
                mobileMenuBtn.children[1].style.opacity = "1";
                mobileMenuBtn.children[2].style.transform = "none";
            }
        });
    }

    // ==========================================
    // 2. ANIMACIONES AL HACER SCROLL
    // ==========================================
    const scrollElements = document.querySelectorAll('.animate-on-scroll');
    const elementInView = (el, dividend = 1) => {
        const elementTop = el.getBoundingClientRect().top;
        return (elementTop <= (window.innerHeight || document.documentElement.clientHeight) / dividend);
    };
    const displayScrollElement = (element) => { element.classList.add('in-view'); };
    const handleScrollAnimation = () => {
        scrollElements.forEach((el) => { if (elementInView(el, 1.25)) { displayScrollElement(el); } });
    };

    handleScrollAnimation();
    window.addEventListener('scroll', () => { handleScrollAnimation(); });

    // ==========================================
    // 3. LÓGICA DE MODALES (ABRIR Y CERRAR)
    // ==========================================
    const modalTriggers = document.querySelectorAll('.modal-trigger');
    const closeButtons = document.querySelectorAll('.close-modal');
    const modals = document.querySelectorAll('.modal-backdrop');

    modalTriggers.forEach(trigger => {
        trigger.addEventListener('click', (e) => {
            e.preventDefault();
            const modalId = trigger.getAttribute('data-modal');
            const targetModal = document.getElementById(modalId);
            if (targetModal) {
                targetModal.classList.add('active');
                document.body.style.overflow = 'hidden'; 
            }
        });
    });

    closeButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const modal = btn.closest('.modal-backdrop');
            if(modal) {
                modal.classList.remove('active');
                document.body.style.overflow = 'auto'; 
            }
        });
    });

    modals.forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
                document.body.style.overflow = 'auto';
            }
        });
    });

    const closeFeedbackBtns = document.querySelectorAll('.close-feedback-btn');
    closeFeedbackBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const modal = btn.closest('.modal-backdrop');
            if(modal) {
                modal.classList.remove('active');
                document.body.style.overflow = 'auto';
            }
        });
    });

    // ==========================================
    // 4. LÓGICA DE FORMULARIOS MÁGICOS (LOGIN/REGISTRO)
    // ==========================================
    const magicForms = document.querySelectorAll('.magic-form');
    const magicOverlay = document.getElementById('magicOverlay');

    magicForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault(); 
            
            const activeModal = document.querySelector('.modal-backdrop.active');
            if (activeModal) activeModal.classList.remove('active');

            if (magicOverlay) magicOverlay.classList.add('active');

            const formData = new FormData(form);
            const url = form.getAttribute('action');

            fetch(url, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.json())
            .then(data => {
                setTimeout(() => {
                    if(data.status === 'success' && data.redirect_url) {
                        window.location.href = data.redirect_url;
                        return; 
                    }

                    if (magicOverlay) magicOverlay.classList.remove('active');
                    
                    const feedbackModal = document.getElementById('modalFeedback');
                    const feedbackIcon = document.getElementById('feedbackIcon');
                    const feedbackTitle = document.getElementById('feedbackTitle');
                    const feedbackMessage = document.getElementById('feedbackMessage');
                    
                    if(data.status === 'success') {
                        form.reset(); 
                        feedbackIcon.innerHTML = '<i class="fas fa-check-circle" style="color: var(--color-brand-teal);"></i>';
                        feedbackTitle.innerText = '¡Todo listo!';
                        feedbackMessage.innerText = data.message;
                    } else {
                        if (data.error_type === 'unverified') {
                            feedbackIcon.innerHTML = '<i class="fas fa-frown" style="color: var(--color-magenta-pink);"></i>';
                            feedbackTitle.innerText = 'Aún no es momento';
                        } else {
                            feedbackIcon.innerHTML = '<i class="fas fa-exclamation-triangle" style="color: var(--color-orange-rust);"></i>';
                            feedbackTitle.innerText = 'Algo salió mal';
                        }
                        feedbackMessage.innerText = data.message;
                    }
                    
                    feedbackModal.classList.add('active');
                    document.body.style.overflow = 'hidden';

                }, 2000);
            })
            .catch(error => {
                setTimeout(() => {
                    if (magicOverlay) magicOverlay.classList.remove('active');
                    alert("Error de conexión con el servidor.");
                }, 2000);
            });
        });
    });

    // ==========================================
    // 5. LÓGICA DEL PANEL DEL PACIENTE (CITAS)
    // ==========================================
    const diasDataElement = document.getElementById('dias-data');
    if (diasDataElement) {
        
        const horariosData = JSON.parse(diasDataElement.textContent);
        const fechasDisponibles = Object.keys(horariosData);
        
        // 🔥 FIX: VARIABLES GLOBALES PARA QUE EL HTML "RÁPIDO" PUEDA INYECTAR LOS DATOS 🔥
        window.animoSeleccionado = "No especificó";
        window.fechaSeleccionada = "";
        window.horaSeleccionada = "";
        window.selectedDayText = "";

        // Rastreador de Ánimo
        const moodBtnsHuge = document.querySelectorAll('.mood-btn-huge');
        moodBtnsHuge.forEach(btn => {
            btn.addEventListener('click', function() {
                moodBtnsHuge.forEach(b => b.classList.remove('selected'));
                this.classList.add('selected');
                window.animoSeleccionado = this.getAttribute('data-animo'); 
            });
        });

        // El Carrusel de Cuadritos
        const dateChips = document.querySelectorAll('.date-chip');
        const slotContainers = document.querySelectorAll('.slots-container');
        
        // Configuramos la fecha por defecto
        const firstChip = document.querySelector('.date-chip.active');
        if (firstChip) {
            window.selectedDayText = firstChip.querySelector('.day-name').innerText + " " + firstChip.querySelector('.day-number').innerText;
            window.fechaSeleccionada = firstChip.getAttribute('data-fecha'); 
        }

        dateChips.forEach(chip => {
            chip.addEventListener('click', function() {
                dateChips.forEach(c => c.classList.remove('active'));
                slotContainers.forEach(container => container.style.display = 'none');
                
                this.classList.add('active');
                const targetId = this.getAttribute('data-target');
                const containerReal = document.getElementById(targetId);
                if(containerReal) containerReal.style.display = 'grid'; // Corregido a grid
                
                window.selectedDayText = this.querySelector('.day-name').innerText + " " + this.querySelector('.day-number').innerText;
                window.fechaSeleccionada = this.getAttribute('data-fecha');
            });
        });

        // ==========================================
        // El Calendario Flatpickr (CORREGIDO)
        // ==========================================
        if(document.getElementById('calendario-picker')) {
            flatpickr("#calendario-picker", {
                locale: "es",
                minDate: "today",
                disableMobile: true,
                enable: fechasDisponibles,
                dateFormat: "Y-m-d",
                onChange: function(selectedDates, dateStr) {
                    const targetChip = document.querySelector(`.date-chip[data-fecha="${dateStr}"]`);
                    if(targetChip) {
                        targetChip.click();
                        targetChip.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                    }
                }
            });
        }

        // Seleccionar Hora y Pasar a Pagar
        const timeSlotBtns = document.querySelectorAll('.time-slot-btn');
        const step1 = document.getElementById('step-1-schedule');
        const step2 = document.getElementById('step-2-payment');
        
        timeSlotBtns.forEach(btn => {
            btn.addEventListener('click', function() {
                // Quitamos la clase 'active' de todos los botones y se la ponemos al actual
                timeSlotBtns.forEach(b => b.classList.remove('active'));
                this.classList.add('active');

                const horaText = this.innerText;
                window.horaSeleccionada = this.getAttribute('data-hora'); 
                window.fechaSeleccionada = this.getAttribute('data-fecha'); // Extraemos la fecha del botón directamente
                
                document.getElementById('selected-time-text').innerHTML = `Reservando para el <strong>${window.selectedDayText || window.fechaSeleccionada}</strong> a las <strong>${horaText}</strong>`;
                document.getElementById('display-date').innerText = window.selectedDayText || window.fechaSeleccionada;
                document.getElementById('display-time').innerText = horaText;

                step1.style.display = 'none';
                step2.style.display = 'block';
                step2.style.animation = 'fadeInUp 0.5s ease forwards';
            });
        });

        // Botón regresar
        const btnBackSchedule = document.getElementById('btn-back-schedule');
        if (btnBackSchedule) {
            btnBackSchedule.addEventListener('click', () => {
                step2.style.display = 'none';
                step1.style.display = 'block';
            });
        }

        // ==========================================
        // Botón de Pagar y Guardar (BLINDADO)
        // ==========================================
        const btnSimulatePay = document.getElementById('btn-simulate-pay');
        if (btnSimulatePay) {
            btnSimulatePay.addEventListener('click', function() {
                
                // 🔥 DOBLE SEGURO: Si las variables están vacías, leemos el HTML directamente 🔥
                const botonActivo = document.querySelector('.time-slot-btn.active');
                if (botonActivo) {
                    window.fechaSeleccionada = botonActivo.getAttribute('data-fecha');
                    window.horaSeleccionada = botonActivo.getAttribute('data-hora');
                }

                if (!window.fechaSeleccionada || !window.horaSeleccionada) {
                    alert("¡Ups! Parece que no se seleccionó la hora correctamente. Por favor, elige un horario.");
                    return;
                }

                document.getElementById('step-2-payment').style.display = 'none';
                document.getElementById('step-3-loading').style.display = 'block';

                let formData = new URLSearchParams();
                formData.append('fecha', window.fechaSeleccionada);
                formData.append('hora', window.horaSeleccionada);
                formData.append('animo', window.animoSeleccionado);
                
                const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
                if(csrfInput) {
                    formData.append('csrfmiddlewaretoken', csrfInput.value);
                }

                fetch("/guardar-cita/", {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
                    body: formData.toString()
                })
                .then(response => response.json())
                .then(data => {
                    if(data.status === 'success') {
                        setTimeout(() => {
                            document.getElementById('booking-wizard').style.display = 'none';
                            document.getElementById('confirmed-appointment').style.display = 'block';
                        }, 1500);
                    } else {
                        alert('Error del servidor: ' + data.message);
                        location.reload(); 
                    }
                })
                .catch(error => {
                    alert('Error de conexión. Intenta de nuevo.');
                    location.reload();
                });
            });
        }
    }

    // ==========================================
    // CONTROL DE FLECHAS DEL CARRUSEL DE FECHAS
    // ==========================================
    const miCarrusel = document.getElementById('dateCarousel');
    const btnPrev = document.getElementById('btn-prev-week');
    const btnNext = document.getElementById('btn-next-week');

    if (miCarrusel && btnPrev && btnNext) {
        const cantidadScroll = 400; 

        btnNext.addEventListener('click', () => {
            miCarrusel.scrollBy({ left: cantidadScroll, behavior: 'smooth' });
        });

        btnPrev.addEventListener('click', () => {
            miCarrusel.scrollBy({ left: -cantidadScroll, behavior: 'smooth' });
        });
    }

});